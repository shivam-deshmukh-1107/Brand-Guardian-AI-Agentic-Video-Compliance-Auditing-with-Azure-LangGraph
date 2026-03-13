# backend/src/api/server.py
'''
Code for Brand Guardian AI API Server
'''

import uuid        # Generate unique session IDs
import logging     # Application logging
# FastAPI = modern web framework
# HTTPException = handles errors with proper HTTP status codes
from fastapi import FastAPI, HTTPException  

# Pydantic = Data validation and serialization library (ensures API requests have correct format)
from pydantic import BaseModel

# Type hints for better code clarity and auto-completion
from typing import List, Optional 

# Loading environment variables from .env file
from dotenv import load_dotenv
load_dotenv(override=True)

# Initializing Telemetry for Azure Monitor
from backend.src.api.telemetry import setup_telemetry
# Activating the sensors - starts tracking all API activity
setup_telemetry()  

# Importing LangGraph Workflow (Indexer → Auditor)
# Renamed to 'compliance_graph' to avoid confusion with FastAPI's 'app'
from backend.src.graph.workflow import app as compliance_graph

# Configuring Logging
logging.basicConfig(level=logging.INFO)  
# Setting default log level (INFO = important events, not debug spam)
logger = logging.getLogger("api-server")


# Creating FASTAPI Application
app = FastAPI(
    # Metadata for auto-generated API documentation (Swagger UI)
    title="Brand Guardian AI API",
    description="API for auditing video content against brand compliance rules.",
    version="1.0.0"
)
# FastAPI automatically creates:
# - Interactive docs at http://localhost:8000/docs
# - OpenAPI schema at http://localhost:8000/openapi.json


# Defining Data Models (Pydantic)
# Request Model
class AuditRequest(BaseModel):
    """
    Defines the expected structure of incoming API requests.
    
    Pydantic validates that:
    - The request contains a 'video_url' field
    - The value is a string (not int, list, etc.)
    
    Example valid request:
    {
        "video_url": "https://youtu.be/abc123"
    }
    
    Example invalid request (raises 422 error):
    {
        "video_url": 12345  ← Not a string!
    }
    """
    video_url: str  # Required string field


# Nested Model
class ComplianceIssue(BaseModel):
    """
    Defines the structure of a single compliance violation.
    
    Used inside AuditResponse to represent each violation found.
    """
    category: str      # Example: "Misleading Claims"
    severity: str      # Example: "CRITICAL"
    description: str   # Example: "Absolute guarantee detected at 00:32"


# Response Model
class AuditResponse(BaseModel):
    """
    Defines the structure of API responses.
    
    FastAPI uses this to:
    1. Validate the response before sending (catches bugs)
    2. Auto-generate API documentation (shows users what to expect)
    3. Provide type hints for frontend developers
    
    Example response:
    {
        "session_id": "ce6c43bb-c71a-4f16-a377-8b493502fee2",
        "video_id": "vid_ce6c43bb",
        "status": "FAIL",
        "final_report": "Video contains 2 critical violations...",
        "compliance_results": [
            {
                "category": "Misleading Claims",
                "severity": "CRITICAL",
                "description": "Absolute guarantee at 00:32"
            }
        ]
    }
    """
    session_id: str                           # Unique audit session ID
    video_id: str                             # Shortened video identifier
    status: str                               # PASS or FAIL
    final_report: str                         # AI-generated summary
    compliance_results: List[ComplianceIssue] # List of violations (can be empty)


# Defining Main Endpoint
@app.post("/audit", response_model=AuditResponse)
# @app.post = Decorator that registers this function as a POST endpoint
# "/audit" = URL path (http://localhost:8000/audit)
# response_model = Tells FastAPI to validate response matches AuditResponse

async def audit_video(request: AuditRequest):
    """
    Main API endpoint that triggers the compliance audit workflow.
    
    HTTP Method: POST
    URL: http://localhost:8000/audit
    
    Request Body:
    {
        "video_url": "https://youtu.be/abc123"
    }
    
    Response: AuditResponse object (defined above)
    
    Process:
    1. Generate unique session ID
    2. Prepare input for LangGraph workflow
    3. Invoke the graph (Indexer → Auditor)
    4. Return formatted results
    """
    
    # Generating Session ID ==========
    session_id = str(uuid.uuid4())  
    
    video_id_short = f"vid_{session_id[:8]}"  
    # Takes first 8 characters: "vid_ce6c43bb"
    # Easier to reference in logs/UI than full UUID
    
    # Logging Incoming Request
    logger.info(f"Received Audit Request: {request.video_url} (Session: {session_id})")

    # Preparing Graph Input
    initial_inputs = {
        "video_url": request.video_url,  # From the API request
        "video_id": video_id_short,      # Generated ID
        "compliance_results": [],        # Will be populated by Auditor
        "errors": []                     # Tracks any processing errors
    }

    try:
        # Invoking Langgraph Workflow
        # This is the same logic from main.py - just wrapped in an API
        final_state = compliance_graph.invoke(initial_inputs) # type: ignore
        # Flow: START → Indexer → Auditor → END
        # Returns: Final state dictionary with all results
        
        # NOTE: In production, we will use:
        # await compliance_graph.ainvoke(initial_inputs)
        # ↑ Async version - doesn't block the server while processing
        
        # Mapping Graph Output TO API Response
        return AuditResponse(
            session_id=session_id,
            video_id=final_state.get("video_id", "None"),  
            # .get() safely retrieves value (None if missing)
            
            status=final_state.get("final_status", "UNKNOWN"),  
            # Defaults to "UNKNOWN" if key doesn't exist
            
            final_report=final_state.get("final_report", "No report generated."),
            
            compliance_results=final_state.get("compliance_results", [])
            # Returns empty list [] if no violations
        )
        # FastAPI automatically converts this Pydantic object to JSON

    except Exception as e:
        # If any error occurs during graph execution, we catch it here
        logger.error(f"Audit Failed: {str(e)}")  
        # Logging the error for debugging
        
        raise HTTPException(
            status_code=500,  # 500 = Internal Server Error
            detail=f"Workflow Execution Failed: {str(e)}"
            # Returns this error message to the client
        )
        # Example error response:
        # {
        #     "detail": "Workflow Execution Failed: YouTube download error"
        # }


# Health Check Endpoint
@app.get("/health")
# GET request at http://localhost:8000/health

def health_check():
    """
    Simple endpoint to verify the API is running.
    
    Used by:
    - Load balancers (to check if server is alive)
    - Monitoring systems (uptime checks)
    - Developers (quick test that server started)
    
    Example usage:
    curl http://localhost:8000/health
    
    Response:
    {
        "status": "healthy",
        "service": "Brand Guardian AI"
    }
    """
    return {"status": "healthy", "service": "Brand Guardian AI"}
    # FastAPI automatically converts dict to JSON response
