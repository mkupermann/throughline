"""Public diagnostic vocabulary. Never expose provider bodies or CLI stderr."""

MESSAGES = {
    "bridge_busy": "The host CLI bridge is busy with another request. Wait for it to finish, then test again.",
    "bridge_auth": "The host CLI bridge rejected its token. Check the matching bridge token on the host and application.",
    "bridge_unreachable": "The application cannot reach the host CLI bridge. Check its address, host service and container network.",
    "bridge_not_configured": "The host CLI bridge is not configured. Set its address and token in the application environment.",
    "cli_timeout": "The host CLI exceeded the test time limit. Wait for the current request to finish and retry.",
    "cli_auth": "The selected host CLI is not authenticated. Sign in with that CLI on the host, then test again.",
    "cli_model": "The host CLI rejected the selected model. Check the model ID, configured alias and account access.",
    "cli_quota": "The host CLI reported a usage or rate limit. Check the service allowance and retry later.",
    "cli_failed": "The host CLI failed. Check its host login, selected model and installed CLI version.",
    "cli_output": "The host CLI returned no valid structured final answer. Check the selected model and retry.",
    "provider_auth": "The selected API rejected authentication or access. Check its saved credentials and model entitlement.",
    "provider_model": "The selected API could not find the endpoint or model. Check the provider address and model ID.",
    "provider_quota": "The selected API reported a usage or rate limit. Check its allowance and retry later.",
    "provider_unreachable": "The selected API is unreachable. Check its address, service and network connection.",
    "provider_failed": "The selected API returned an error. Check the service status and configuration.",
}


class AIConnectionError(RuntimeError):
    def __init__(self, code):
        self.code = code if code in MESSAGES else "cli_failed"
        super().__init__(MESSAGES[self.code])
