from urllib.parse import quote

import httpx


class AirflowClient:
    def __init__(self, settings, client=None):
        self.settings, self.client = settings, client

    async def trigger(self, dag_id: str, conf: dict, run_id: str, approved: bool):
        if not approved or dag_id not in self.settings.airflow_allowed_dags:
            raise PermissionError("Approved allowlisted DAG required")
        if not self.settings.airflow_token.get_secret_value():
            raise ValueError("AIRFLOW_TOKEN is required")
        client = self.client or httpx.AsyncClient(timeout=30)
        try:
            response = await client.post(
                f"{self.settings.airflow_base_url}/dags/{quote(dag_id, safe='')}/dagRuns",
                headers={
                    "Authorization": "Bearer " + self.settings.airflow_token.get_secret_value()
                },
                json={"dag_run_id": "lifeos-" + run_id, "conf": conf},
            )
            if response.status_code == 409:
                return {"duplicate": True}
            response.raise_for_status()
            return response.json()
        finally:
            if self.client is None:
                await client.aclose()
