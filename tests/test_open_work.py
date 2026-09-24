"""get_open_work (task 38; svamanas item 12): the whole open tree in one call."""
import os
import sqlite3

import pytest

from aimfp.database.connection import clear_project_root_cache
from aimfp.helpers.orchestrators.entry_points import aimfp_init
from aimfp.helpers.orchestrators.status import assemble_open_work, get_open_work


@pytest.fixture
def root(tmp_path):
    clear_project_root_cache()
    assert aimfp_init(str(tmp_path), init_git=False).success
    conn = sqlite3.connect(os.path.join(tmp_path, ".aimfp-project", "project.db"))
    conn.executescript("""
        INSERT INTO completion_path (id, name, order_index, status) VALUES (1, 'p', 1, 'in_progress');
        INSERT INTO milestones (id, completion_path_id, name, status) VALUES (1, 1, 'm1', 'in_progress');
        INSERT INTO milestones (id, completion_path_id, name, status) VALUES (2, 1, 'm2', 'pending');
        INSERT INTO tasks (id, milestone_id, name, status, priority, description)
            VALUES (1, 1, 'done', 'completed', 'high', 'x'),
                   (2, 1, 'blocked one', 'blocked', 'critical', 'b'),
                   (3, 1, 'pending low', 'pending', 'low', 'p'),
                   (4, 1, 'active', 'in_progress', 'medium', 'a long description'),
                   (5, 2, 'other milestone', 'pending', 'high', 'o');
        INSERT INTO subtasks (id, parent_task_id, name, status) VALUES
            (1, 4, 'open sub', 'pending'), (2, 4, 'closed sub', 'completed'), (3, 1, 'sub of done', 'pending');
        INSERT INTO sidequests (id, paused_task_id, name, status) VALUES
            (1, 4, 'urgent fix', 'in_progress'), (2, 5, 'other sq', 'pending'), (3, 4, 'old sq', 'completed');
        INSERT INTO items (reference_table, reference_id, name, status) VALUES
            ('tasks', 4, 'item a', 'completed'), ('tasks', 4, 'item b', 'pending'),
            ('tasks', 3, 'item c', 'in_progress'), ('subtasks', 1, 'sub item', 'pending'),
            ('sidequests', 1, 'sq item', 'pending'), ('tasks', 1, 'done item', 'pending');
    """)
    conn.commit()
    conn.close()
    yield str(tmp_path)
    clear_project_root_cache()


def test_open_tree(root):
    r = get_open_work(project_root=root)
    assert r.success, r.error
    tasks = r.data["tasks"]
    assert [t["name"] for t in tasks] == ["active", "other milestone", "pending low", "blocked one"]
    active = tasks[0]
    assert [i["name"] for i in active["items"]] == ["item b"]
    assert [s["name"] for s in active["subtasks"]] == ["open sub"]
    assert [i["name"] for i in active["subtasks"][0]["items"]] == ["sub item"]
    assert "description" not in active
    assert [s["name"] for s in r.data["sidequests"]] == ["urgent fix", "other sq"]
    assert r.data["sidequests"][0]["items"][0]["name"] == "sq item"
    assert r.data["counts"] == {"tasks": 4, "subtasks": 1, "sidequests": 2, "open_items": 4}


def test_milestone_filter(root):
    r = get_open_work(milestone_id=2, project_root=root)
    assert [t["name"] for t in r.data["tasks"]] == ["other milestone"]
    assert [s["name"] for s in r.data["sidequests"]] == ["other sq"]


def test_descriptions_on_request(root):
    r = get_open_work(include_descriptions=True, project_root=root)
    assert r.data["tasks"][0]["description"] == "a long description"


def test_empty_project(tmp_path):
    clear_project_root_cache()
    assert aimfp_init(str(tmp_path), init_git=False).success
    r = get_open_work(project_root=str(tmp_path))
    assert r.success and r.data["tasks"] == () and r.data["counts"]["open_items"] == 0


def test_assemble_is_pure():
    out = assemble_open_work(({"id": 1, "name": "t", "status": "pending"},), (), (),
                             ({"id": 9, "name": "i", "status": "pending",
                               "reference_table": "tasks", "reference_id": 1},))
    assert out["tasks"][0]["items"] == ({"id": 9, "name": "i", "status": "pending"},)
