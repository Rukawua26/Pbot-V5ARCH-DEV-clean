import unittest
from unittest.mock import patch

from tools.github_projects_kanban import (
    DEFAULT_PROJECT_TITLE,
    ESTRATEGIAS_ACTIVAS,
    ORDENES_PENDIENTES,
    GitHubProjectsError,
    GitHubProjectsKanbanClient,
    actualizar_pnl_tarjeta,
    crear_tablero_kanban,
)


class GitHubProjectsKanbanTests(unittest.TestCase):
    def setUp(self):
        self.client = GitHubProjectsKanbanClient(
            token="token",
            project_id="project-123",
        )

    def test_crear_tarjeta_operacion_creates_draft_and_sets_status(self):
        with (
            patch.object(
                self.client,
                "_get_status_field",
                return_value=type(
                    "StatusField",
                    (),
                    {"field_id": "field-1", "options_by_name": {ESTRATEGIAS_ACTIVAS: "opt-1"}},
                )(),
            ),
            patch.object(self.client, "_set_status") as mocked_set_status,
            patch.object(
                self.client,
                "_graphql",
                return_value={
                    "addProjectV2DraftIssue": {
                        "projectItem": {"id": "item-1", "content": {"id": "draft-1"}}
                    }
                },
            ),
        ):
            result = self.client.crear_tarjeta_operacion("BTC/USDT", "Breakout", 25)

        self.assertTrue(result.ok)
        self.assertEqual(result.item_id, "item-1")
        self.assertEqual(result.draft_issue_id, "draft-1")
        self.assertEqual(result.column, ESTRATEGIAS_ACTIVAS)
        mocked_set_status.assert_called_once()

    def test_mover_tarjeta_rejects_unknown_column(self):
        with patch.object(
            self.client,
            "_get_status_field",
            return_value=type(
                "StatusField",
                (),
                {"field_id": "field-1", "options_by_name": {ORDENES_PENDIENTES: "opt-2"}},
            )(),
        ):
            result = self.client.mover_tarjeta("item-1", "No Existe")

        self.assertFalse(result.ok)
        self.assertIn("Columna invalida", result.error)

    def test_actualizar_pnl_tarjeta_updates_draft_issue_body(self):
        item_payload = {
            "content": {
                "__typename": "DraftIssue",
                "id": "draft-1",
                "body": "## Seguimiento en Vivo\n- Precio actual: Pendiente\n- PnL actual: Pendiente",
            }
        }

        with (
            patch.object(self.client, "_get_item_with_content", return_value=item_payload),
            patch.object(
                self.client,
                "_graphql",
                return_value={"updateProjectV2DraftIssue": {"draftIssue": {"id": "draft-1"}}},
            ) as mocked_graphql,
        ):
            result = self.client.actualizar_pnl_tarjeta("item-1", 15.7, 103450.5)

        self.assertTrue(result.ok)
        self.assertEqual(result.draft_issue_id, "draft-1")
        variables = mocked_graphql.call_args.args[1]
        self.assertIn("15.7", variables["body"])
        self.assertIn("103450.5", variables["body"])

    def test_actualizar_pnl_tarjeta_wrapper_returns_error_without_env(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(GitHubProjectsError):
                GitHubProjectsKanbanClient()

        with patch.dict("os.environ", {}, clear=True):
            result = actualizar_pnl_tarjeta("item-1", 1, 2)

        self.assertFalse(result["ok"])
        self.assertIn("GITHUB_TOKEN", result["error"])

    def test_configurar_tablero_kanban_updates_status_and_returns_url(self):
        with (
            patch.object(
                self.client,
                "_fetch_status_field",
                return_value={"id": "field-1", "options": [{"id": "old", "name": "Todo"}]},
            ),
            patch.object(self.client, "_update_status_field_options") as mocked_update_status,
            patch.object(
                self.client,
                "_update_project_metadata",
            ) as mocked_update_metadata,
            patch.object(
                self.client,
                "_get_project_summary",
                return_value={
                    "id": "project-123",
                    "number": 7,
                    "title": DEFAULT_PROJECT_TITLE,
                    "url": "https://github.com/users/Rukawua26/projects/7",
                },
            ),
            patch.object(
                self.client,
                "_get_status_field",
                return_value=type(
                    "StatusField",
                    (),
                    {
                        "field_id": "field-1",
                        "options_by_name": {
                            ESTRATEGIAS_ACTIVAS: "a",
                            ORDENES_PENDIENTES: "b",
                        },
                    },
                )(),
            ),
        ):
            result = self.client.configurar_tablero_kanban(
                title=DEFAULT_PROJECT_TITLE, public=False
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.project_number, 7)
        self.assertIn("projects/7", result.url)
        mocked_update_status.assert_called_once_with("field-1")
        mocked_update_metadata.assert_called_once()

    def test_crear_tablero_kanban_creates_and_configures_project(self):
        with (
            patch.object(self.client, "_resolve_owner_id", return_value="owner-1"),
            patch.object(
                self.client,
                "_resolve_repository_id",
                return_value="repo-1",
            ),
            patch.object(
                self.client,
                "_graphql",
                return_value={
                    "createProjectV2": {
                        "projectV2": {
                            "id": "project-999",
                            "number": 12,
                            "url": "https://github.com/users/Rukawua26/projects/12",
                            "title": DEFAULT_PROJECT_TITLE,
                        }
                    }
                },
            ) as mocked_graphql,
            patch.object(
                self.client,
                "configurar_tablero_kanban",
                return_value=type("Result", (), {"ok": True})(),
            ),
        ):
            result = self.client.crear_tablero_kanban(
                owner_login="Rukawua26",
                repo_full_name="Rukawua26/Pbot-V5ARCH-DEV",
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.project_number, 12)
        self.assertEqual(mocked_graphql.call_args.args[1]["repositoryId"], "repo-1")

    def test_crear_tablero_kanban_wrapper_returns_error_without_env(self):
        with patch.dict("os.environ", {}, clear=True):
            result = crear_tablero_kanban()

        self.assertFalse(result["ok"])
        self.assertIn("GITHUB_TOKEN", result["error"])


if __name__ == "__main__":
    unittest.main()
