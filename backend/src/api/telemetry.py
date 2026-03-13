# backend/src/api/telemetry.py
'''
Azure's OpenTelemetry integration - tracks app performance, errors, requests
- This module sets up Azure Monitor OpenTelemetry for the Brand Guardian Compliance QA Pipeline API.

Why do we need telemetry?
Without:
- No visibility into API performance (response times, error rates)
    - API is Slow -> No idea why (database, code, external service?)
    - Errors happen -> No insight into what or where
- No insights into user behavior (which endpoints are most used)
    - How many users today? -> No visibility into traffic patterns
    - Which endpoints are popular? -> No data to optimize or prioritize features

With:
- Information available about the runtimes
    -  /audit endpoint averages 4.5s (Indexers takes 3.8s, code takes 0.7s)
- Error logs with stack traces
    - 500 errors on /compliance -> Database connection timeout
    - 12% audits fails due to Youtube Download errors -> Need to investigate external dependency
- Metrics Show: 450 API calls/day, 80% to /audit -> Focus on optimizing audit endpoint
'''

import os
import logging      # Python's built-in logging system
from azure.monitor.opentelemetry import configure_azure_monitor  

# Creating a Dedicated Logger to separate telemetry logs from your main application logs
logger = logging.getLogger("brand-guardian-telemetry")
# Example log output: "brand-guardian-telemetry - INFO - Azure Monitor enabled"


def setup_telemetry():
    """
    Initializes Azure Monitor OpenTelemetry.
    
    What is OpenTelemetry?
    - Industry-standard observability framework
    - Tracks: HTTP requests, database queries, errors, performance metrics
    - Sends this data to Azure Monitor (like a "flight data recorder" for your app)
    
    Its hooks into FastAPI automatically:
    - Once configured, it auto-captures every API request/response
    - No need to manually log each endpoint
    - Tracks response times, error rates, dependencies (like Azure Search calls)
    """
    
    # Retrieving the Connection String from environment variables
    connection_string = os.getenv("APPLICATION_INSIGHTS_CONNECTION_STRING")
    
    if not connection_string:
        logger.warning("No Instrumentation Key found. Telemetry is DISABLED.")
        return  # Exit function to stop configuring the Azure Monitor

    # Configuring Azure Monitor
    try:
        # configure_azure_monitor() does the heavy lifting:
        # 1. Registers automatic instrumentation for:
        #    - HTTP requests (FastAPI endpoints)
        #    - Database calls (Azure Search queries)
        #    - Logging events
        # 2. Starts background thread to send data to Azure
        configure_azure_monitor(
            connection_string=connection_string,  # Where to send data in Azure
            logger_name="brand-guardian-tracer"   # Custom tracer name for better log correlation
        )
        
        logger.info(" Azure Monitor Tracking Enabled & Connected!")
        
    except Exception as e:
        # Handling potential errors during configuration (e.g., invalid connection string, network issues)
        # Note:
        # Function doesn't raise the error - telemetry failure shouldn't crash the app
        logger.error(f"Failed to initialize Azure Monitor: {e}")