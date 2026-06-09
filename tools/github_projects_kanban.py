"""Integracion ligera con GitHub Projects v2 para representar trades como Kanban.

Este modulo crea tarjetas tipo Draft Issue dentro de un Project v2 y permite
moverlas entre columnas de estado y actualizar su seguimiento de PnL.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"
STATUS_FIELD_NAME = "Status"
ESTRATEGIAS_ACTIVAS = "Estrategias Activas"
ORDENES_PENDIENTES = "Órdenes Pendientes"
POSICIONES_ABIERTAS = "Posiciones Abiertas"
HISTORIAL_CIERRE = "Historial de Cierre"
DEFAULT_PROJECT_TITLE = "Trading Operations Kanban"
ALLOWED_COLUMNS = {
    ESTRATEGIAS_ACTIVAS,
    ORDENES_PENDIENTES,
    POSICIONES_ABIERTAS,
    HISTORIAL_CIERRE,
}
STATUS_OPTIONS = (
    {
        "name": ESTRATEGIAS_ACTIVAS,
        "description": "Buscar señales",
        "color": "BLUE",
    },
    {
        "name": ORDENES_PENDIENTES,
        "description": "Limit/Stop en el exchange",
        "color": "ORANGE",
    },
    {
        "name": POSICIONES_ABIERTAS,
        "description": "Trades en ejecución con PnL vivo",
        "color": "GREEN",
    },
    {
        "name": HISTORIAL_CIERRE,
        "description": "Operaciones finalizadas",
        "color": "PURPLE",
    },
)


class GitHubProjectsError(RuntimeError):
    """Error controlado para fallas de configuracion o API de GitHub."""


@dataclass(slots=True)
class GitHubProjectActionResult:
    ok: bool
    action: str
    item_id: str | None = None
    draft_issue_id: str | None = None
    project_id: str | None = None
    project_number: int | None = None
    url: str | None = None
    column: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ProjectStatusField:
    field_id: str
    options_by_name: dict[str, str]


class GitHubProjectsKanbanClient:
    """Cliente minimo para Project v2 usando GraphQL y tarjetas Draft Issue."""

    def __init__(
        self,
        token: str | None = None,
        project_id: str | None = None,
        project_number: int | None = None,
        project_owner: str | None = None,
        timeout_seconds: int = 10,
    ) -> None:
        self._token = token or os.getenv("GITHUB_TOKEN")
        self._project_id = project_id or os.getenv("GITHUB_PROJECT_ID")
        self._project_owner = project_owner or os.getenv("GITHUB_PROJECT_OWNER")
        self._timeout_seconds = timeout_seconds
        self._project_number = project_number or _read_int_env("GITHUB_PROJECT_NUMBER")
        self._status_field_cache: ProjectStatusField | None = None

        if not self._token:
            raise GitHubProjectsError("Falta GITHUB_TOKEN en el entorno.")

    def crear_tarjeta_operacion(
        self, par_activo: str, estrategia: str, capital: float | int
    ) -> GitHubProjectActionResult:
        try:
            project_id = self._ensure_project_id()
            status_field = self._get_status_field(project_id)
            title = f"{par_activo} | {estrategia}"
            body = self._build_trade_body(par_activo, estrategia, capital)
            query = """
            mutation($projectId: ID!, $title: String!, $body: String!) {
              addProjectV2DraftIssue(
                input: {
                  projectId: $projectId,
                  title: $title,
                  body: $body
                }
              ) {
                projectItem {
                  id
                  content {
                    ... on DraftIssue {
                      id
                    }
                  }
                }
              }
            }
            """
            data = self._graphql(
                query,
                {"projectId": project_id, "title": title, "body": body},
            )
            project_item = data["addProjectV2DraftIssue"]["projectItem"]
            item_id = project_item["id"]
            draft_issue_id = project_item["content"]["id"]
            self._set_status(
                project_id,
                item_id,
                status_field,
                ESTRATEGIAS_ACTIVAS,
            )
            return GitHubProjectActionResult(
                ok=True,
                action="crear_tarjeta_operacion",
                item_id=item_id,
                draft_issue_id=draft_issue_id,
                project_id=project_id,
                column=ESTRATEGIAS_ACTIVAS,
            )
        except Exception as exc:
            return self._error_result("crear_tarjeta_operacion", exc)

    def mover_tarjeta(
        self, tarjeta_id: str, columna_destino: str
    ) -> GitHubProjectActionResult:
        try:
            project_id = self._ensure_project_id()
            status_field = self._get_status_field(project_id)
            self._set_status(project_id, tarjeta_id, status_field, columna_destino)
            return GitHubProjectActionResult(
                ok=True,
                action="mover_tarjeta",
                item_id=tarjeta_id,
                project_id=project_id,
                column=columna_destino,
            )
        except Exception as exc:
            return self._error_result("mover_tarjeta", exc, item_id=tarjeta_id, column=columna_destino)

    def actualizar_pnl_tarjeta(
        self, tarjeta_id: str, pnl_actual: float | int, precio_actual: float | int
    ) -> GitHubProjectActionResult:
        try:
            project_id = self._ensure_project_id()
            item_data = self._get_item_with_content(tarjeta_id)
            content = item_data["content"]
            if content.get("__typename") != "DraftIssue":
                raise GitHubProjectsError(
                    "Solo se puede actualizar el cuerpo de tarjetas creadas como Draft Issue."
                )

            updated_body = self._merge_live_metrics(
                content.get("body") or "",
                pnl_actual=pnl_actual,
                precio_actual=precio_actual,
            )
            mutation = """
            mutation($draftIssueId: ID!, $body: String!) {
              updateProjectV2DraftIssue(
                input: {
                  draftIssueId: $draftIssueId,
                  body: $body
                }
              ) {
                draftIssue {
                  id
                }
              }
            }
            """
            self._graphql(
                mutation,
                {"draftIssueId": content["id"], "body": updated_body},
            )
            return GitHubProjectActionResult(
                ok=True,
                action="actualizar_pnl_tarjeta",
                item_id=tarjeta_id,
                draft_issue_id=content["id"],
                project_id=project_id,
                column=POSICIONES_ABIERTAS,
            )
        except Exception as exc:
            return self._error_result("actualizar_pnl_tarjeta", exc, item_id=tarjeta_id)

    def crear_tablero_kanban(
        self,
        title: str = DEFAULT_PROJECT_TITLE,
        owner_login: str | None = None,
        repo_full_name: str | None = None,
        public: bool = False,
    ) -> GitHubProjectActionResult:
        try:
            owner_login = owner_login or os.getenv("GITHUB_PROJECT_OWNER")
            if not owner_login:
                raise GitHubProjectsError(
                    "Configura GITHUB_PROJECT_OWNER o pasa owner_login para crear el tablero."
                )

            owner_id = self._resolve_owner_id(owner_login)
            repository_id = None
            repo_full_name = repo_full_name or os.getenv("GITHUB_REPOSITORY")
            if repo_full_name:
                repository_id = self._resolve_repository_id(repo_full_name)

            mutation = """
            mutation($ownerId: ID!, $title: String!, $repositoryId: ID) {
              createProjectV2(
                input: {
                  ownerId: $ownerId,
                  title: $title,
                  repositoryId: $repositoryId
                }
              ) {
                projectV2 {
                  id
                  number
                  url
                  title
                }
              }
            }
            """
            data = self._graphql(
                mutation,
                {
                    "ownerId": owner_id,
                    "title": title,
                    "repositoryId": repository_id,
                },
            )
            project = data["createProjectV2"]["projectV2"]
            self._project_id = project["id"]
            self._project_number = project["number"]
            self._status_field_cache = None

            configured = self.configurar_tablero_kanban(
                project_id=project["id"],
                title=title,
                public=public,
            )
            if not configured.ok:
                return configured

            return GitHubProjectActionResult(
                ok=True,
                action="crear_tablero_kanban",
                project_id=project["id"],
                project_number=project["number"],
                url=project["url"],
            )
        except Exception as exc:
            return self._error_result("crear_tablero_kanban", exc)

    def configurar_tablero_kanban(
        self,
        project_id: str | None = None,
        title: str | None = None,
        public: bool | None = None,
    ) -> GitHubProjectActionResult:
        try:
            project_id = project_id or self._ensure_project_id()
            status_field = self._fetch_status_field(project_id)
            self._update_status_field_options(status_field["id"])
            self._status_field_cache = None
            self._update_project_metadata(project_id, title=title, public=public)
            project = self._get_project_summary(project_id)
            self._get_status_field(project_id)
            return GitHubProjectActionResult(
                ok=True,
                action="configurar_tablero_kanban",
                project_id=project["id"],
                project_number=project["number"],
                url=project["url"],
            )
        except Exception as exc:
            return self._error_result("configurar_tablero_kanban", exc)

    def _error_result(
        self,
        action: str,
        exc: Exception,
        item_id: str | None = None,
        column: str | None = None,
    ) -> GitHubProjectActionResult:
        return GitHubProjectActionResult(
            ok=False,
            action=action,
            item_id=item_id,
            project_id=self._project_id,
            column=column,
            error=str(exc),
        )

    def _ensure_project_id(self) -> str:
        if self._project_id:
            return self._project_id

        project = self._query_project_for_owner_type("user")
        if not project:
            project = self._query_project_for_owner_type("organization")
        if not project:
            raise GitHubProjectsError(
                "No se pudo resolver el proyecto. Verifica owner, numero y permisos del token."
            )
        self._project_id = project["id"]
        return self._project_id

    def _get_status_field(self, project_id: str) -> ProjectStatusField:
        if self._status_field_cache is not None:
            return self._status_field_cache

        field = self._fetch_status_field(project_id)
        options_by_name = {option["name"]: option["id"] for option in field["options"]}
        missing_columns = sorted(ALLOWED_COLUMNS - set(options_by_name))
        if missing_columns:
            raise GitHubProjectsError(
                "Faltan columnas requeridas en Status: " + ", ".join(missing_columns)
            )

        self._status_field_cache = ProjectStatusField(
            field_id=field["id"],
            options_by_name=options_by_name,
        )
        return self._status_field_cache

    def _fetch_status_field(self, project_id: str) -> dict[str, Any]:
        if not project_id:
            raise GitHubProjectsError("Falta project_id para consultar el campo Status.")

        query = """
        query($projectId: ID!) {
          node(id: $projectId) {
            ... on ProjectV2 {
              field(name: \"Status\") {
                ... on ProjectV2SingleSelectField {
                  id
                  options {
                    id
                    name
                  }
                }
              }
            }
          }
        }
        """
        data = self._graphql(query, {"projectId": project_id})
        field = data["node"]["field"]
        if not field:
            raise GitHubProjectsError("El proyecto no tiene un campo Status.")
        return field

    def _set_status(
        self,
        project_id: str,
        item_id: str,
        status_field: ProjectStatusField,
        target_column: str,
    ) -> None:
        if target_column not in ALLOWED_COLUMNS:
            raise GitHubProjectsError(
                "Columna invalida. Usa una de: " + ", ".join(sorted(ALLOWED_COLUMNS))
            )

        option_id = status_field.options_by_name.get(target_column)
        if not option_id:
            raise GitHubProjectsError(f"La columna '{target_column}' no existe en Status.")

        mutation = """
        mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $optionId: String!) {
          updateProjectV2ItemFieldValue(
            input: {
              projectId: $projectId,
              itemId: $itemId,
              fieldId: $fieldId,
              value: { singleSelectOptionId: $optionId }
            }
          ) {
            projectV2Item {
              id
            }
          }
        }
        """
        self._graphql(
            mutation,
            {
                "projectId": project_id,
                "itemId": item_id,
                "fieldId": status_field.field_id,
                "optionId": option_id,
            },
        )

    def _get_item_with_content(self, item_id: str) -> dict[str, Any]:
        query = """
        query($itemId: ID!) {
          node(id: $itemId) {
            ... on ProjectV2Item {
              id
              content {
                __typename
                ... on DraftIssue {
                  id
                  title
                  body
                }
                ... on Issue {
                  id
                  title
                  body
                }
              }
            }
          }
        }
        """
        data = self._graphql(query, {"itemId": item_id})
        item = data.get("node")
        if not item:
            raise GitHubProjectsError(f"No se encontro la tarjeta '{item_id}'.")
        return item

    def _resolve_owner_id(self, owner_login: str) -> str:
        owner = self._query_owner_for_type(owner_login, "user")
        if not owner:
            owner = self._query_owner_for_type(owner_login, "organization")
        if owner:
            return owner["id"]
        raise GitHubProjectsError(f"No se pudo resolver el owner '{owner_login}'.")

    def _query_owner_for_type(self, owner_login: str, owner_type: str) -> dict[str, Any] | None:
        query = f"""
        query($login: String!) {{
          {owner_type}(login: $login) {{
            id
          }}
        }}
        """
        data = self._graphql(query, {"login": owner_login})
        return data.get(owner_type)

    def _query_project_for_owner_type(self, owner_type: str) -> dict[str, Any] | None:
        query = f"""
        query($login: String!, $number: Int!) {{
          {owner_type}(login: $login) {{
            projectV2(number: $number) {{
              id
            }}
          }}
        }}
        """
        data = self._graphql(
            query,
            {"login": self._project_owner, "number": self._project_number},
        )
        owner = data.get(owner_type)
        if not owner:
            return None
        return owner.get("projectV2")

    def _resolve_repository_id(self, repo_full_name: str) -> str:
        owner, name = _split_repo_full_name(repo_full_name)
        query = """
        query($owner: String!, $name: String!) {
          repository(owner: $owner, name: $name) {
            id
          }
        }
        """
        data = self._graphql(query, {"owner": owner, "name": name})
        repository = data.get("repository")
        if not repository:
            raise GitHubProjectsError(f"No se pudo resolver el repositorio '{repo_full_name}'.")
        return repository["id"]

    def _update_status_field_options(self, field_id: str) -> None:
        mutation = """
        mutation($fieldId: ID!, $options: [ProjectV2SingleSelectFieldOptionInput!]!) {
          updateProjectV2Field(
            input: {
              fieldId: $fieldId,
              name: \"Status\",
              singleSelectOptions: $options
            }
          ) {
            projectV2Field {
              ... on ProjectV2SingleSelectField {
                id
              }
            }
          }
        }
        """
        self._graphql(mutation, {"fieldId": field_id, "options": list(STATUS_OPTIONS)})

    def _update_project_metadata(
        self,
        project_id: str,
        title: str | None = None,
        public: bool | None = None,
    ) -> None:
        if title is None and public is None:
            return

        mutation = """
        mutation(
          $projectId: ID!,
          $title: String,
          $public: Boolean,
          $shortDescription: String,
          $readme: String
        ) {
          updateProjectV2(
            input: {
              projectId: $projectId,
              title: $title,
              public: $public,
              shortDescription: $shortDescription,
              readme: $readme
            }
          ) {
            projectV2 {
              id
            }
          }
        }
        """
        self._graphql(
            mutation,
            {
                "projectId": project_id,
                "title": title,
                "public": public,
                "shortDescription": "Kanban de operaciones para el bot de trading.",
                "readme": self._build_project_readme(),
            },
        )

    def _get_project_summary(self, project_id: str) -> dict[str, Any]:
        query = """
        query($projectId: ID!) {
          node(id: $projectId) {
            ... on ProjectV2 {
              id
              number
              title
              url
            }
          }
        }
        """
        data = self._graphql(query, {"projectId": project_id})
        project = data.get("node")
        if not project:
            raise GitHubProjectsError(f"No se pudo leer el proyecto '{project_id}'.")
        return project

    def _graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
        }
        response = requests.post(
            GITHUB_GRAPHQL_URL,
            json={"query": query, "variables": variables},
            headers=headers,
            timeout=self._timeout_seconds,
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise GitHubProjectsError(
                f"Respuesta no JSON de GitHub ({response.status_code})."
            ) from exc

        if response.status_code >= 400:
            errors = payload.get("errors") or payload
            raise GitHubProjectsError(
                f"GitHub GraphQL devolvio HTTP {response.status_code}: {errors}"
            )

        if payload.get("errors"):
            raise GitHubProjectsError(f"GitHub GraphQL devolvio errores: {payload['errors']}")

        return payload["data"]

    @staticmethod
    def _build_trade_body(par_activo: str, estrategia: str, capital: float | int) -> str:
        return "\n".join(
            [
                "## Operacion",
                f"- Par: {par_activo}",
                f"- Estrategia: {estrategia}",
                f"- Capital asignado: {capital} USDT",
                "",
                "## Estado de Flujo",
                f"- Columna actual: {ESTRATEGIAS_ACTIVAS}",
                "",
                "## Seguimiento en Vivo",
                "- Precio actual: Pendiente",
                "- PnL actual: Pendiente",
            ]
        )

    @staticmethod
    def _merge_live_metrics(
        body: str,
        pnl_actual: float | int,
        precio_actual: float | int,
    ) -> str:
        pnl_line = f"- PnL actual: {pnl_actual}"
        precio_line = f"- Precio actual: {precio_actual}"
        updated = _replace_or_append_line(body, r"^- PnL actual:.*$", pnl_line)
        updated = _replace_or_append_line(updated, r"^- Precio actual:.*$", precio_line)
        if "## Seguimiento en Vivo" not in updated:
            updated = updated.rstrip() + "\n\n## Seguimiento en Vivo\n" + precio_line + "\n" + pnl_line
        return updated

    @staticmethod
    def _build_project_readme() -> str:
        return "\n".join(
            [
                "# Trading Operations Kanban",
                "",
                "Este tablero representa el ciclo de vida de las operaciones del bot.",
                "",
                "## Columnas",
                f"- {ESTRATEGIAS_ACTIVAS}: buscar señales.",
                f"- {ORDENES_PENDIENTES}: órdenes limit/stop esperando ejecución.",
                f"- {POSICIONES_ABIERTAS}: trades activos con PnL en vivo.",
                f"- {HISTORIAL_CIERRE}: operaciones finalizadas.",
            ]
        )


def crear_tarjeta_operacion(
    par_activo: str,
    estrategia: str,
    capital: float | int,
) -> dict[str, Any]:
    """Crea una tarjeta de operacion en la columna Estrategias Activas."""

    try:
        client = GitHubProjectsKanbanClient()
        return client.crear_tarjeta_operacion(par_activo, estrategia, capital).to_dict()
    except Exception as exc:
        return GitHubProjectActionResult(
            ok=False,
            action="crear_tarjeta_operacion",
            error=str(exc),
        ).to_dict()


def mover_tarjeta(tarjeta_id: str, columna_destino: str) -> dict[str, Any]:
    """Mueve una tarjeta existente a otra columna del tablero."""

    try:
        client = GitHubProjectsKanbanClient()
        return client.mover_tarjeta(tarjeta_id, columna_destino).to_dict()
    except Exception as exc:
        return GitHubProjectActionResult(
            ok=False,
            action="mover_tarjeta",
            item_id=tarjeta_id,
            column=columna_destino,
            error=str(exc),
        ).to_dict()


def actualizar_pnl_tarjeta(
    tarjeta_id: str,
    pnl_actual: float | int,
    precio_actual: float | int,
) -> dict[str, Any]:
    """Actualiza el cuerpo de una tarjeta Draft Issue con precio y PnL."""

    try:
        client = GitHubProjectsKanbanClient()
        return client.actualizar_pnl_tarjeta(tarjeta_id, pnl_actual, precio_actual).to_dict()
    except Exception as exc:
        return GitHubProjectActionResult(
            ok=False,
            action="actualizar_pnl_tarjeta",
            item_id=tarjeta_id,
            error=str(exc),
        ).to_dict()


def crear_tablero_kanban(
    title: str = DEFAULT_PROJECT_TITLE,
    owner_login: str | None = None,
    repo_full_name: str | None = None,
    public: bool = False,
) -> dict[str, Any]:
    """Crea y configura un GitHub Project v2 con flujo Kanban de trading."""

    try:
        client = GitHubProjectsKanbanClient()
        return client.crear_tablero_kanban(
            title=title,
            owner_login=owner_login,
            repo_full_name=repo_full_name,
            public=public,
        ).to_dict()
    except Exception as exc:
        return GitHubProjectActionResult(
            ok=False,
            action="crear_tablero_kanban",
            error=str(exc),
        ).to_dict()


def configurar_tablero_kanban(
    project_id: str | None = None,
    title: str | None = None,
    public: bool | None = None,
) -> dict[str, Any]:
    """Configura el tablero existente para usar las columnas requeridas."""

    try:
        client = GitHubProjectsKanbanClient(project_id=project_id)
        return client.configurar_tablero_kanban(
            project_id=project_id,
            title=title,
            public=public,
        ).to_dict()
    except Exception as exc:
        return GitHubProjectActionResult(
            ok=False,
            action="configurar_tablero_kanban",
            project_id=project_id,
            error=str(exc),
        ).to_dict()


def _read_int_env(name: str) -> int | None:
    raw_value = os.getenv(name)
    if raw_value in (None, ""):
        return None
    try:
        return int(raw_value or "0")
    except ValueError as exc:
        raise GitHubProjectsError(f"La variable {name} debe ser un entero.") from exc


def _replace_or_append_line(body: str, pattern: str, replacement: str) -> str:
    if re.search(pattern, body, flags=re.MULTILINE):
        return re.sub(pattern, replacement, body, flags=re.MULTILINE)
    if not body.strip():
        return replacement
    return body.rstrip() + "\n" + replacement


def _split_repo_full_name(repo_full_name: str) -> tuple[str, str]:
    parts = repo_full_name.split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise GitHubProjectsError(
            "GITHUB_REPOSITORY debe tener formato 'owner/repo'."
        )
    return parts[0], parts[1]
