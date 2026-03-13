# Brand Guardian AI - Video Indexer Service
'''
Connector: Python and Azure Video Indexer
'''

import os
import time
import logging
from typing import Any, cast
import uuid
import requests
import yt_dlp  # Helps to download YouTube videos
from azure.identity import DefaultAzureCredential # For authentication
from azure.storage.blob import BlobServiceClient, generate_blob_sas, BlobSasPermissions # For Azure Blob Storage interactions
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode
from pathlib import Path
from dotenv import load_dotenv

_ENV_PATH = Path(__file__).resolve().parents[3] / ".env"
load_dotenv(_ENV_PATH, override=True)

logger = logging.getLogger("video-indexer")

# Transient exceptions that are always safe to retry
_TRANSIENT = (
    requests.exceptions.ConnectionError,
    requests.exceptions.ChunkedEncodingError,
    requests.exceptions.Timeout,
    OSError,
)

# Generic retry helper with linear backoff for transient errors
def _retry(fn, *, attempts: int = 3, base_wait: int = 10, label: str = "request"):
    """
    Generic retry helper with linear backoff.
    Retries on transient network errors only.
    HTTPError is re-raised immediately (caller decides recoverability).
    """
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except requests.exceptions.HTTPError:
            raise  # never silently retry HTTP errors
        except _TRANSIENT as e:
            last_exc = e
            wait = base_wait * attempt
            logger.warning(f"{label} attempt {attempt}/{attempts} failed: {e} — retrying in {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"{label} failed after {attempts} attempts: {last_exc}") from last_exc

# Class to handle video indexing
class VideoIndexerService:
    # Function to initialize the service
    def __init__(self):
        def _require(key: str) -> str:
            val = os.getenv(key)
            if not val:
                raise RuntimeError(f"Missing required environment variable: {key}")
            return val

        self.account_id      = _require("AZURE_VI_ACCOUNT_ID")
        self.location        = _require("AZURE_VI_LOCATION")
        self.subscription_id = _require("AZURE_SUBSCRIPTION_ID")
        self.resource_group  = _require("AZURE_RESOURCE_GROUP")
        self.storage_account = _require("AZURE_STORAGE_ACCOUNT_NAME")
        self.storage_key     = _require("AZURE_STORAGE_ACCOUNT_KEY")

        self.vi_name        = os.getenv("AZURE_VI_NAME", "brand-guardian-ai-video-indexer")
        self.blob_container = os.getenv("AZURE_BLOB_CONTAINER", "brand-guardian-videos")

        self.credential = DefaultAzureCredential()

    # ------------------------------------------------------------------ 
    #  Auth                                                                
    # ------------------------------------------------------------------ 

    # Function to get Azure Token
    def get_access_token(self) -> str:
        """Generates an ARM access token (cached + auto-refreshed by SDK)."""
        try:
            token_object = self.credential.get_token("https://management.azure.com/.default")
            return token_object.token
        except Exception as e:
            logger.error(f"Failed to get Azure Token: {e}")
            raise

    # Function to get Video Indexer Account Token
    def get_account_token(self, arm_access_token):
        """Exchanges ARM token for Video Indexer Account Token, with retry."""
        url = (
            f"https://management.azure.com/subscriptions/{self.subscription_id}"
            f"/resourceGroups/{self.resource_group}"
            f"/providers/Microsoft.VideoIndexer/accounts/{self.vi_name}"
            f"/generateAccessToken?api-version=2025-04-01"
        )
        headers = {"Authorization": f"Bearer {arm_access_token}"}
        payload = {"permissionType": "Contributor", "scope": "Account"}

        # This call can fail due to transient network issues, so we wrap it in our retry helper.
        def _call():
            r = requests.post(url, headers=headers, json=payload, timeout=30)
            if r.status_code != 200:
                raise requests.exceptions.HTTPError(
                    f"Failed to get VI token [{r.status_code}]: {r.text}", response=r
                )
            return r.json().get("accessToken")
        
        # Use a custom retry function here to handle transient errors gracefully, while still surfacing HTTP errors immediately.
        return _retry(_call, attempts=3, base_wait=5, label="get_account_token")

    def _fresh_vi_token(self) -> str:
        """Convenience: ARM → VI token in one call."""
        return self.get_account_token(self.get_access_token())

    # ------------------------------------------------------------------ 
    #  YouTube Download                                                    
    # ------------------------------------------------------------------ 

    def download_youtube_video(self, url: str, output_path: str ="temp_video.mp4") -> str:
        """Downloads a YouTube video to a local file."""
        logger.info(f"Downloading YouTube video: {url}")
        
        ydl_opts = {
            'format': 'best', # Download the video in mp4 format or the best available format
            'outtmpl': output_path, # output template
            'quiet': False, # Suppress logs
            'no_warnings': False, # Suppress warnings
            'overwrites': True, # Overwrite existing files
            "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
            "http_headers":  {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            },
        }
        
        try:
            with yt_dlp.YoutubeDL(cast(Any, ydl_opts)) as ydl:
                ydl.download([url])
            logger.info("Download complete.")
            return output_path
        except Exception as e:
            raise Exception(f"YouTube Download Failed: {str(e)}")

    # ------------------------------------------------------------------ 
    #  Blob Storage                                                        
    # ------------------------------------------------------------------ 
    # Function to upload video to Blob Storage
    def upload_to_blob(self, local_path: str, sas_expiry_hours: int = 4) -> str:
        """
        Uploads the local video file to Azure Blob Storage and returns
        a time-limited SAS URL that Video Indexer can pull from.
        """
        blob_name = f"audit-{uuid.uuid4()}.mp4"

        logger.info(f"Uploading {local_path} → blob '{blob_name}'")

        blob_service = BlobServiceClient(
            account_url=f"https://{self.storage_account}.blob.core.windows.net",
            credential=self.storage_key   
        )

        # Creating container if it doesn't already exist
        container_client = blob_service.get_container_client(self.blob_container)
        if not container_client.exists():
            container_client.create_container()
            logger.info(f"Created blob container: {self.blob_container}")

        # Stream upload (handles large files safely)
        with open(local_path, "rb") as data:
            container_client.upload_blob(
                name=blob_name,
                data=data,
                overwrite=True,
                max_concurrency=4       # parallel chunks for speed
            )

        # SAS token valid for 4 hours — enough for VI to pull and process
        sas_token = generate_blob_sas(
            account_name=self.storage_account,
            container_name=self.blob_container,
            blob_name=blob_name,
            account_key=self.storage_key,
            permission=BlobSasPermissions(read=True),
            expiry=datetime.now(timezone.utc) + timedelta(hours=sas_expiry_hours)
        )

        sas_url = (
            f"https://{self.storage_account}.blob.core.windows.net"
            f"/{self.blob_container}/{blob_name}?{sas_token}"
        )
        logger.info(f"Blob SAS URL generated (valid {sas_expiry_hours}h).")
        return sas_url

    # ------------------------------------------------------------------ 
    #  VI Submission                                                       
    # ------------------------------------------------------------------ 
    # Function to upload video to Azure Video Indexer
    def upload_video(self, video_path: str, video_name: str) -> str:
        """
        Uploads video in the blob storage to Azure Video Indexer. 
        Refreshes VI token on EVERY retry attempt
        so a stale JWT never causes a silent 401.
        """
        sas_url = self.upload_to_blob(video_path)
        api_base = (
            f"https://api.videoindexer.ai/{self.location}"
            f"/Accounts/{self.account_id}/Videos"
        )

        last_exc: Exception | None = None

        for attempt in range(1, 4):
            try:
                # Refreshing token on every attempt — JWT in URL must be current
                vi_token = self._fresh_vi_token()

                query_params = {
                    "accessToken":         vi_token,
                    "name":                video_name,
                    "privacy":             "Private",
                    "indexingPreset":      "Default",
                    "videoUrl":            sas_url,
                    "sendCompletionEmail": "false",
                }
                full_url = f"{api_base}?{urlencode(query_params)}"

                logger.info(f"Submitting to Azure Video Indexer (attempt {attempt}/3): {video_name}")

                with requests.Session() as session:
                    session.headers.update({
                        "Connection":   "close",
                        "Content-Type": "application/json",
                    })
                    response = session.post(full_url, timeout=60)

                response.raise_for_status()
                body = response.json()

                video_id = body.get("id")
                if not video_id:
                    raise ValueError(f"VI response missing 'id': {body}")

                logger.info(f"Video accepted by VI. Azure Video ID: {video_id}")
                return video_id

            except requests.exceptions.HTTPError as e:
                sc = e.response.status_code if e.response is not None else 0
                # 429 = rate limit → retry; anything else → fatal
                if sc == 429:
                    wait = 30 * attempt
                    logger.warning(f"VI rate limited (429), retrying in {wait}s...")
                    time.sleep(wait)
                    continue
                raise RuntimeError(
                    f"VI submission HTTP error [{sc}]: {e.response.text if e.response else e}"
                ) from e

            except _TRANSIENT as e:
                last_exc = e
                wait = 10 * attempt  # 10s, 20s, 30s
                logger.warning(
                    f"Connection reset on VI submission attempt {attempt}/3: {e}"
                    f" — retrying in {wait}s"
                )
                time.sleep(wait)

        raise RuntimeError(f"VI submission failed after 3 attempts: {last_exc}")

    # ------------------------------------------------------------------ 
    #  Polling                                                             
    # ------------------------------------------------------------------ 
    # Function to wait for video processing
    def wait_for_processing(
        self,
        video_id: str,
        max_consecutive_errors: int = 5,
        initial_wait: int = 45,
        poll_interval: int = 30,
    ) -> dict:
        """
        Polls VI until Processed. Exponential backoff on transient errors.
        initial_wait reduced to 45s — saves time for
        short videos that process in ~50s.
        """
        logger.info(f"Waiting for video {video_id} to process...")
        time.sleep(initial_wait)

        consecutive_errors = 0

        while True:
            try:
                # Fresh token on every poll attempt to avoid silent 401s from expired JWTs
                vi_token = self._fresh_vi_token() 
                url = (
                    f"https://api.videoindexer.ai/{self.location}"
                    f"/Accounts/{self.account_id}/Videos/{video_id}/Index"
                )

                with requests.Session() as session:
                    session.headers.update({"Connection": "close"})
                    response = session.get(
                        url,
                        params={"accessToken": vi_token},
                        timeout=30,
                    )

                response.raise_for_status()
                data  = response.json()
                state = data.get("state")
                consecutive_errors = 0  # reset on any success

                if state == "Processed":
                    logger.info(f"Video {video_id} processing complete.")
                    return data
                elif state == "Failed":
                    raise RuntimeError(f"Azure VI failed for video: {video_id}")
                elif state == "Quarantined":
                    raise RuntimeError("Video quarantined — copyright/content policy violation.")

                logger.info(f"Status: {state}... waiting {poll_interval}s")
                time.sleep(poll_interval)

            except requests.exceptions.HTTPError as e:
                sc = e.response.status_code if e.response is not None else 0
                if sc in (401, 429):
                    consecutive_errors += 1
                    backoff = poll_interval * (2 ** consecutive_errors)
                    logger.warning(f"HTTP {sc} polling VI — retrying in {backoff}s...")
                    time.sleep(backoff)
                else:
                    raise

            except _TRANSIENT as e:
                consecutive_errors += 1
                backoff = poll_interval * (2 ** consecutive_errors)
                logger.warning(
                    f"Connection error polling VI "
                    f"(attempt {consecutive_errors}/{max_consecutive_errors}): {e}"
                    f" — retrying in {backoff}s"
                )
                if consecutive_errors >= max_consecutive_errors:
                    raise RuntimeError(
                        f"Max retries exceeded polling VI for {video_id}: {e}"
                    ) from e
                time.sleep(backoff)

    # ------------------------------------------------------------------ 
    #  Data Extraction                                                     
    # ------------------------------------------------------------------ 
    # Function to extract data from JSON
    def extract_data(self, vi_json: dict) -> dict:
        """Parses VI JSON into pipeline state format."""
        transcript_lines = [
            insight["text"]
            for v in vi_json.get("videos", [])
            for insight in v.get("insights", {}).get("transcript", [])
            if insight.get("text")  # guard against None entries
        ]

        ocr_lines = [
            insight["text"]
            for v in vi_json.get("videos", [])
            for insight in v.get("insights", {}).get("ocr", [])
            if insight.get("text")  # guard against None entries
        ]

        duration = (
            vi_json.get("summarizedInsights", {})
                   .get("duration", {})
                   .get("seconds")
        )

        return {
            "transcript":     " ".join(transcript_lines),
            "ocr_text":       ocr_lines,
            "video_metadata": {"duration": duration, "platform": "youtube"},
        }