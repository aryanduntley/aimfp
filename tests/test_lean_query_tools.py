"""
Lean query tools from live-session feedback (task 74).

- Milestone getters: detail_level (lean blanks descriptions) and
  include_task_counts ({status: n, 'total': n}) from one grouped query.
- get_all_flows: flow_ids filter + detail_level; unknown IDs are named.
- get_tasks_by_milestone: detail_level + status filter.
- audit_tracking: sample_limit and full=[categories] control the inline sample.
- Function parameters come back in one parsed shape whatever was stored.
"""
import json
import os
import sqlite3

import pytest

from aimfp.database.connection import clear_project_root_cache
from aimfp.helpers.audit.reports import summarize_findings, validate_sample_options
from aimfp.helpers.audit.tracking_audit import audit_tracking
from aimfp.helpers.catalog.extract import normalize_recorded_parameters, recorded_parameter_facts
from aimfp.helpers.orchestrators.entry_points import aimfp_init
from aimfp.helpers.project.functions_1 import get_function_by_name, normalize_function_row
from aimfp.helpers.project.functions_2 import get_functions_by_file
from aimfp.helpers.project.metadata import create_project
from aimfp.helpers.project.tasks import (
    get_incomplete_milestones,
    get_milestones_by_path,
    get_milestones_by_status,
    get_tasks_by_milestone,
    task_counts_by_milestone,
)
from aimfp.helpers.project.themes_flows_1 import get_all_flows


@pytest.fixture
def project(tmp_path, monkeypatch):
    clear_project_root_cache()
    root = tmp_path / "calc"
    root.mkdir()
    assert aimfp_init(str(root), init_git=False).success
    monkeypatch.chdir(root)
    assert create_project("Calc", "A calculator", ["add"], project_root=str(root)).success
    c = sqlite3.connect(os.path.join(str(root), ".aimfp-project", "project.db"))
    c.execute("INSERT INTO completion_path (id, name, order_index) VALUES (1, 'p', 1)")
    c.execute("INSERT INTO milestones (id, completion_path_id, name, status, description) "
              "VALUES (1, 1, 'm1', 'in_progress', 'long m1'), (2, 1, 'm2', 'pending', 'long m2'), "
              "(3, 1, 'm3', 'completed', 'long m3')")
    c.executemany("INSERT INTO tasks (milestone_id, name, status, description) VALUES (?, ?, ?, 'long')",
                  [(1, 'a', 'completed'), (1, 'b', 'pending'), (1, 'c', 'pending'), (3, 'd', 'completed')])
    c.execute("INSERT INTO flows (id, name, description) VALUES (3, 'F3', 'flow three'), (7, 'F7', 'flow seven'), "
              "(9, 'F9', 'flow nine')")
    c.execute("INSERT INTO files (id, name, path) VALUES (1, 'a.py', 'src/a.py')")
    legacy = json.dumps(json.dumps(["x: int", "y: str = 'k'", "*rest"]))   # JSON wrapped in JSON
    c.execute("INSERT INTO functions (name, file_id, parameters, returns) VALUES ('legacy', 1, ?, ?)",
              (legacy, json.dumps(json.dumps({"type": "int"}))))
    c.execute("INSERT INTO functions (name, file_id, parameters) VALUES ('catalog', 1, ?)",
              (json.dumps([{"name": "x", "type": "int", "default": None, "kind": "positional"},
                           {"name": "y", "type": "int", "default": "2", "kind": "positional"}]),))
    c.commit()
    c.close()
    yield str(root)
    clear_project_root_cache()


# ----------------------------------------------------------------------------
# milestones
# ----------------------------------------------------------------------------

def test_task_counts_have_every_status():
    counts = task_counts_by_milestone(((1, 'pending', 2), (1, 'completed', 1)))
    assert counts == {1: {'pending': 2, 'in_progress': 0, 'blocked': 0, 'completed': 1, 'total': 3}}


def test_milestones_are_lean_by_default_with_optional_counts(project):
    lean = get_incomplete_milestones(project_root=project)
    assert lean.success, lean.error
    assert [(m.id, m.description, m.task_counts) for m in lean.milestones] == [(1, None, None), (2, None, None)]

    full = get_incomplete_milestones(project_root=project, detail_level='full', include_task_counts=True)
    by_id = {m.id: m for m in full.milestones}
    assert by_id[1].description == 'long m1'
    assert by_id[1].task_counts == {'pending': 2, 'in_progress': 0, 'blocked': 0, 'completed': 1, 'total': 3}
    assert by_id[2].task_counts['total'] == 0                     # no tasks: zeros, not missing

    path = get_milestones_by_path(1, project_root=project, include_task_counts=True)
    assert {m.id: m.task_counts['total'] for m in path.milestones} == {1: 3, 2: 0, 3: 1}
    done = get_milestones_by_status('completed', project_root=project, include_task_counts=True)
    assert [(m.id, m.task_counts['completed']) for m in done.milestones] == [(3, 1)]


def test_milestone_getters_reject_bad_detail_level(project):
    assert not get_incomplete_milestones(project_root=project, detail_level='huge').success
    assert not get_milestones_by_path(1, project_root=project, detail_level='huge').success
    assert not get_milestones_by_status('bogus', project_root=project).success


# ----------------------------------------------------------------------------
# tasks by milestone
# ----------------------------------------------------------------------------

def test_tasks_by_milestone_lean_full_and_status(project):
    lean = get_tasks_by_milestone(1, project_root=project)
    assert lean.success, lean.error
    assert sorted(t.name for t in lean.tasks) == ['a', 'b', 'c']
    assert {t.description for t in lean.tasks} == {None}

    pending = get_tasks_by_milestone(1, project_root=project, status='pending', detail_level='full')
    assert sorted(t.name for t in pending.tasks) == ['b', 'c']
    assert {t.description for t in pending.tasks} == {'long'}
    assert not get_tasks_by_milestone(1, project_root=project, status='done').success


# ----------------------------------------------------------------------------
# flows
# ----------------------------------------------------------------------------

def test_flows_by_id_and_detail_level(project):
    every = get_all_flows(project_root=project)
    assert {f.id for f in every.flows} >= {3, 7, 9}
    assert {f.description for f in every.flows} == {None}

    some = get_all_flows(project_root=project, flow_ids=[7, 3, 7, 404], detail_level='full')
    assert some.success, some.error
    assert [(f.id, f.description) for f in some.flows] == [(3, 'flow three'), (7, 'flow seven')]
    assert any('404' in s for s in some.return_statements)
    assert get_all_flows(project_root=project, flow_ids=[]).flows == ()
    assert not get_all_flows(project_root=project, detail_level='huge').success


# ----------------------------------------------------------------------------
# audit sample control
# ----------------------------------------------------------------------------

def test_summarize_findings_sample_size_and_full():
    findings = {'stale_descriptions': tuple(range(11)), 'missing_functions': tuple(range(8)), 'empty': ()}
    default = summarize_findings(findings)
    assert len(default['stale_descriptions']['sample']) == 5 and 'empty' not in default
    shaped = summarize_findings(findings, 2, ('stale_descriptions',))
    assert shaped['stale_descriptions'] == {'count': 11, 'sample': list(range(11))}
    assert shaped['missing_functions']['sample'] == [0, 1]
    assert summarize_findings(findings, 0)['missing_functions'] == {'count': 8, 'sample': []}


def test_sample_options_are_validated(project):
    assert validate_sample_options(5, ('stale_descriptions',)) is None
    assert 'sample_limit' in validate_sample_options(-1, ())
    assert 'sample_limit' in validate_sample_options(True, ())
    assert 'nonsense' in validate_sample_options(5, ('nonsense',))
    assert not audit_tracking(project_root=project, sample_limit=-1).success
    assert 'nonsense' in audit_tracking(project_root=project, full=['nonsense']).error


# ----------------------------------------------------------------------------
# function parameters
# ----------------------------------------------------------------------------

def test_normalize_recorded_parameters_shapes():
    keys = {'name', 'type', 'required', 'default', 'description', 'kind'}
    legacy = normalize_recorded_parameters(json.dumps(json.dumps(["x: int", "y: str = 'k'", "**kw"])))
    assert [set(p) for p in legacy] == [keys] * 3
    assert [(p['name'], p['type'], p['required'], p['default'], p['kind']) for p in legacy] == [
        ('x', 'int', None, None, None), ('y', 'str', False, "'k'", None), ('kw', None, False, None, 'var_keyword')]
    explicit = normalize_recorded_parameters([{"name": "z", "type": "int", "required": True, "description": "d"}])
    assert explicit == [{'name': 'z', 'type': 'int', 'required': True, 'default': None,
                         'description': 'd', 'kind': None}]
    assert normalize_recorded_parameters(None) is None
    assert normalize_recorded_parameters('[]') == []
    assert normalize_recorded_parameters('{"not": "a list"}') == {'not': 'a list'}   # kept, never dropped
    assert recorded_parameter_facts(["*args"]) == (('args', False),)


def test_function_reads_return_one_parameter_shape(project):
    legacy = get_function_by_name('legacy', project_root=project).functions[0]
    assert [p['name'] for p in legacy.parameters] == ['x', 'y', 'rest']
    assert legacy.parameters[2]['kind'] == 'var_positional'
    assert legacy.returns == {'type': 'int'}

    catalog = {f.name: f for f in get_functions_by_file(1, project_root=project).functions}['catalog']
    assert [(p['name'], p['required'], p['default']) for p in catalog.parameters] == [('x', True, None), ('y', False, '2')]

    row = normalize_function_row({'id': 1, 'parameters': '["a: int"]', 'returns': None})
    assert row['parameters'][0]['name'] == 'a' and row['returns'] is None
    assert normalize_function_row({'id': 1}) == {'id': 1}
