"""Deploy agents_meeting_prep to Vertex AI Agent Engine.

Run from gcp_exploration/:
    python scripts/deploy_meeting_prep_agent.py

Resource name is saved to agents_meeting_prep/.deployed_resource_name.
"""

import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PROJECT = os.getenv("GCP_PROJECT_ID", "agentic-wealthadvisor")
LOCATION = os.getenv("GCP_LOCATION", "us-west1")

_COMBINED_CA = Path(__file__).parent.parent / "catalog" / "combined_ca.pem"
if _COMBINED_CA.exists():
    os.environ["REQUESTS_CA_BUNDLE"] = str(_COMBINED_CA)
    os.environ["SSL_CERT_FILE"] = str(_COMBINED_CA)

_SA_KEY = Path(__file__).parent.parent / "catalog" / "agentic-wealthadvisor-9e4f503e15c0.json"
if _SA_KEY.exists():
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(_SA_KEY)

import vertexai
from vertexai import agent_engines

AGENT_DIR = Path(__file__).parent.parent / "agents_meeting_prep"
CONFIG_FILE = AGENT_DIR / ".agent_engine_config.json"

config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
display_name = config["display_name"]
env_vars = config.get("env_vars", {})


def deploy():
    logger.info("Initialising Vertex AI - project=%s location=%s", PROJECT, LOCATION)
    vertexai.init(
        project=PROJECT,
        location=LOCATION,
        staging_bucket=f"gs://{PROJECT}-agent-engine",
    )

    logger.info("Deploying agent from %s", AGENT_DIR)
    logger.info("Display name : %s", display_name)
    logger.info("Env vars     : %s", list(env_vars.keys()))

    sys.path.insert(0, str(AGENT_DIR.parent))
    from agents_meeting_prep.agent import root_agent

    os.chdir(str(AGENT_DIR.parent))

    engine = agent_engines.create(
        agent_engine=root_agent,
        requirements=str(AGENT_DIR / "requirements.txt"),
        display_name=display_name,
        env_vars=env_vars,
        extra_packages=["agents_meeting_prep"],
        service_account="wealthadvisor@agentic-wealthadvisor.iam.gserviceaccount.com",
    )

    logger.info("=== Deployment complete ===")
    logger.info("Resource name : %s", engine.resource_name)

    out = AGENT_DIR / ".deployed_resource_name"
    out.write_text(engine.resource_name, encoding="utf-8")
    logger.info("Saved to      : %s", out)

    return engine


if __name__ == "__main__":
    deploy()
