# Brand_Guardian/ComplianceQAPipeline/backend/src/graph/node.py
import json
import os
import logging
import re # regular expressions for text processing
from typing import List, Dict, Any

from langchain_openai import AzureChatOpenAI, AzureOpenAIEmbeddings
from langchain_community.vectorstores import AzureSearch
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import SystemMessage, HumanMessage

# Importing State Schemas
from backend.src.graph.state import VideoAuditState, ComplianceIssue

# Importing Service
from backend.src.services.video_indexer import VideoIndexerService

# Configuring the Logger
logger = logging.getLogger("brand-guardian")
logging.basicConfig(level=logging.INFO)

# NODE 1 - Indexer
# Function responsible for converting video into text and insights.
def index_video_node(state: VideoAuditState) -> Dict[str, Any]:
    """
    Node to index the video using Azure Video Indexer.
    Downloads the video, uploads it to Azure Video Indexer, extracts the insights.
    """
    video_url = state.get("video_url")
    video_id_input = state.get("video_id", "vid_demo")
    
    logger.info(f"----[Node: Indexer] Processing Video: {video_url}")

    local_filename = "temp_audit_video.mp4"
    try:
        # Initialize Video Indexer Service - creating an instance of the video indexerservice
        vi_service = VideoIndexerService()
        # Downloading the video - using yt-dlp
        if "youtube.com" in video_url or "youtu.be" in video_url:
            local_path = vi_service.download_youtube_video(video_url, output_path=local_filename)
        else:
            raise Exception("Unsupported video URL format. Please provide a valid YouTube URL.")

        # Uploading the video to Azure Video Indexer
        azure_video_id = vi_service.upload_video(local_path, video_name=video_id_input)
        logger.info(f"Upload Sucess! Azure ID: {azure_video_id}")

        # Cleaning up 
        if os.path.exists(local_path):
            os.remove(local_path)

        # Waiting - pauses the code execution and checks the status of the video every 30 seconds
        raw_insights = vi_service.wait_for_processing(azure_video_id)
        
        # Extracting Insights
        clean_data = vi_service.extract_data(raw_insights)
        logger.info("---[Node: Indexer] Extraction Complete ---")
        return clean_data

    except Exception as e:
        logger.error(f"Error indexing video: {e}")
        return {
            "errors": [str(e)],
            "final_status": "FAIL",
            "transcript": "",
            "ocr_text": []
        }

# Node 2 - Compliance Auditor
# This node is responsible for extracting the audio content from the video.
# It uses RAG to extract the audio content from the video and the uses AI to judge the content.
def audio_content_node(state: VideoAuditState) -> Dict[str, Any]:
    """
    Node to extract audio content from the video.
    Performs Retreival Augmented Generation (RAG) to extract audio content - brand Video
    """

    logger.info("----[Node: Audio Content] Quering the Knowledge base & LLM")
    transcript = state.get("transcript", "")
    if not transcript:
        logger.warning("No transcript available, skipping audio content extraction")
        return {
            "final_status": "FAIL",
            "final_report": "Audit skipped because video processing failed (No Transcript Available)."
            }
        
    # Initialize Azure Clients Services
    llm = AzureChatOpenAI(
        azure_deployment=os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT"),
        api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
        temperature=0.2
    )

    # Initialize Azure Search
    embeddings = AzureOpenAIEmbeddings(
        azure_deployment="text-embedding-3-small",
        api_version=os.getenv("AZURE_OPENAI_API_VERSION")
    )

    def require_env(var_name: str) -> str:
        value = os.getenv(var_name)
        if value is None:
            raise RuntimeError(f"Missing required environment variable: {var_name}")
        return value

    vector_store = AzureSearch(
        azure_search_endpoint=require_env("AZURE_SEARCH_ENDPOINT"),
        azure_search_key=require_env("AZURE_SEARCH_API_KEY"),
        index_name=require_env("AZURE_SEARCH_INDEX_NAME"),
        embedding_function=embeddings.embed_query
    )

    # RAG Retrieval
    # This is the process of retrieving the relevant information from the knowledge base.
    # It combines the transcript and ocr_text to one search query to query the vector store and retrieve the relevant information.
    ocr_text = state.get("ocr_text", [])
    query_text = f"{transcript} {''.join(ocr_text)}"
    # Retrieving the top 3 most relevant documents from the knowledge base.
    docs = vector_store.similarity_search(query_text, k=3)
    retrieved_rules = "\n\n".join([doc.page_content for doc in docs])
    
    # LLM Prompting
    # Using the LLM to judge the content of the video based on the retrieved rules.
    system_prompt = f"""
    You are a Senior Brand Compliance Auditor.
    
    OFFICIAL REGULATORY RULES:
    {retrieved_rules}
    
    INSTRUCTIONS:
    1. Analyze the Transcript and OCR text below.
    2. Identify ANY violations of the rules.
    3. Return strictly JSON in the following format:
    
    {{
        "compliance_results": [
            {{
                "category": "Claim Validation",
                "severity": "CRITICAL",
                "description": "Explanation of the violation..."
            }}
        ],
        "status": "FAIL", 
        "final_report": "Summary of findings..."
    }}

    If no violations are found, set "status" to "PASS" and "compliance_results" to [].
    """

    # User Message
    # This is the message that is sent to the LLM along with the system prompt.
    # It contains the transcript and ocr_text of the video.
    user_message = f"""
    VIDEO METADATA: {state.get('video_metadata', {})}
    TRANSCRIPT: {transcript}
    ON-SCREEN TEXT (OCR): {ocr_text}
    """
    
    response = None
    try:
        # Invoking the LLM
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message)
        ])
        
        # Clean Markdown if present (```json ... ```) - Safety Measure to pass only JSON to the next node
        content = response.content

        # Normalizing LangChain content into a plain string for regex/json parsing
        if not isinstance(content, str):
            # If it's a list of parts, pull out the text-ish fields
            parts = []
            for p in content:
                if isinstance(p, dict):
                    # common keys: "text", sometimes nested structures
                    if "text" in p and isinstance(p["text"], str):
                        parts.append(p["text"])
                    else:
                        parts.append(json.dumps(p, ensure_ascii=False))
                else:
                    parts.append(str(p))
            content = "\n".join(parts)

        # Strip ```json ... ``` if present
        if "```" in content:
            m = re.search(r"```(?:json)?\s*(.*?)\s*```", content, flags=re.DOTALL)
            if m:
                content = m.group(1)

        audit_data = json.loads(content.strip())
        
        return {
            "compliance_results": audit_data.get("compliance_results", []),
            "final_status": audit_data.get("status", "FAIL"),
            "final_report": audit_data.get("final_report", "No report generated.")
        }

    except Exception as e:
        logger.error(f"System Error in Auditor Node: {str(e)}")
        # Logging the raw response to see what went wrong
        if response is not None:
            logger.error(f"Raw LLM Response: {response.content}")
        else:
            logger.error("Raw LLM Response: None (invoke failed before response)")
        return {
            "errors": [str(e)],
            "final_status": "FAIL"
        }


