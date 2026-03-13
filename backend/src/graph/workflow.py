# Brand_Guardian/ComplianceQAPipeline/backend/src/graph/workflow.py
'''
This module defines the DAG: Directed Acyclic Graph that orchestrates the video complainece audit process.
It connects the nodes using the StateGraph class from the Langgraph.
It combines the state and nodes together to create a workflow.
This is the main entry point for the application.

START -> INDEX_VIDEO_NODE -> AUDIO_CONTENT_NODE -> END
'''

from langgraph.graph import StateGraph, END
from backend.src.graph.state import VideoAuditState
from backend.src.graph.nodes import (
    index_video_node,
    audio_content_node
)

def create_graph():
    """
    Constructs(creates a new empty graph object) and compiles the LangGraph workflow.
    Returns:
        Compiled Graph: Runnable graph object for execution.
    """
    # Initializing the graph with the state schema - enforces strict data rules
    workflow = StateGraph(VideoAuditState)
    
    # Adding nodes to the graph - represents the steps in the workflow
    workflow.add_node("indexer", index_video_node)
    workflow.add_node("auditor", audio_content_node)

    # Setting entry point: indexer node
    workflow.set_entry_point("indexer")
    
    # Adding edges to the graph - defines the flow of execution
    workflow.add_edge("indexer", "auditor")
    # Once the auditor node is executed, the graph workflow will terminate
    workflow.add_edge("auditor", END)
    
    # Compiling the graph - creates a runnable object
    app = workflow.compile()

    # Returning the compiled graph - ready to run
    return app

# Exposing the compiled graph as a global variable
app = create_graph()