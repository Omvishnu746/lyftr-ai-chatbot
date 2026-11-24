import os

# Set litellm offline and local flags BEFORE any litellm import occurs
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
os.environ["LITELLM_OFFLINE_MODE"] = "true"
os.environ["LITELLM_TELEMETRY"] = "False"
