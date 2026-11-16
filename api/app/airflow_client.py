"""Cliente mínimo de la API REST v2 de Airflow 3 (autenticación JWT).

Todo error (red, credenciales, DAG no encontrado, 5xx) se convierte en `AirflowNoDisponible`:
la API responde 503 y nunca se queda esperando (timeout corto).
"""

import threading

import httpx


class AirflowNoDisponible(Exception):
    pass


class AirflowClient:
    def __init__(self, base_url: str, usuario: str, clave: str, dag_id: str, timeout: float = 5.0, http: httpx.Client | None = None):
        self._usuario, self._clave, self._dag_id = usuario, clave, dag_id
        self._http = http or httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)
        self._token: str | None = None
        self._lock = threading.Lock()

    def _obtener_token(self, renovar: bool = False) -> str:
        with self._lock:
            if self._token is None or renovar:
                r = self._http.post("/auth/token", json={"username": self._usuario, "password": self._clave})
                if r.status_code not in (200, 201):
                    raise AirflowNoDisponible(f"Airflow rechazó la autenticación (HTTP {r.status_code})")
                self._token = r.json()["access_token"]
            return self._token

    def _pedir(self, metodo: str, ruta: str, **kw) -> httpx.Response:
        try:
            for intento in (0, 1):  # un 401 puede ser un token vencido: se renueva una vez
                r = self._http.request(metodo, ruta, headers={"Authorization": f"Bearer {self._obtener_token(renovar=intento == 1)}"}, **kw)
                if r.status_code != 401:
                    return r
            return r
        except (httpx.HTTPError, KeyError, ValueError) as e:
            raise AirflowNoDisponible(f"no se pudo hablar con Airflow: {type(e).__name__}") from e

    def disparar(self, dag_run_id: str) -> None:
        """Crea la ejecución con el id dado (así la API puede guardarlo antes de que corra)."""
        r = self._pedir("POST", f"/api/v2/dags/{self._dag_id}/dagRuns", json={"dag_run_id": dag_run_id, "logical_date": None})
        if r.status_code not in (200, 201):
            raise AirflowNoDisponible(f"Airflow no aceptó el disparo (HTTP {r.status_code})")

    def estado(self, dag_run_id: str) -> str | None:
        """queued | running | success | failed, o None si Airflow no conoce esa ejecución."""
        r = self._pedir("GET", f"/api/v2/dags/{self._dag_id}/dagRuns/{dag_run_id}")
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise AirflowNoDisponible(f"Airflow respondió HTTP {r.status_code}")
        return r.json().get("state")
