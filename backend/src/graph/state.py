# Brand_Guardian/ComplianceQAPipeline/backend/src/graph/state.py
import operator
from typing import Annotated, List, Dict, Optional, Any, TypedDict

# Defining the Schema for a single compliance result  
# Error Report Structure
class ComplianceIssue(TypedDict):
    category: str # Category of violation
    description: str # Specific details of violation
    severity: str # CRITICAL | WARNING
    timestamp: Optional[str] # Timestamp of the violation

# Defining the Global Graph State
# This defines the state that is passed around in the agentic workflow
class VideoAuditState(TypedDict):
    """
    Defines the data schema for the langgraph execution content.
    Main Container: Holds all the information about the video audit process(From the initial URL to Final Report).
    """
    # Input parameters
    video_url: str # Youtube URL
    video_id: str # Youtube Video ID
    
    # Ingestion and Extraction data
    # These fields store the raw data extracted from the video.
    local_file_path: Optional[str] # Local file path of the downloaded video - Temporary Storage
    video_metadata: Dict[str, Any] # Technical Details e.g { "duration": 120, "resolution": "1080p" }
    transcript: Optional[str] # Fully extracted speech to text
    ocr_text: List[str] # Extracted text from images/frames from the video

    # Analysis Output
    # Stores the list of all the violations found in the video by the LLM.
    compliance_results: Annotated[List[ComplianceIssue], operator.add] # LLM generated summary of violations

    # Final Deliverables to the User
    final_status: str # PASS | FAIL
    final_report: str # Markdown File

    # System Observability
    # Errors: API timeout, system level errors
    # List of System-level Crashes e.g download fail, azure timeout, etc.
    errors: Annotated[List[str], operator.add]
    