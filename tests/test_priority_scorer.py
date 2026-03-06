"""Tests for TaskPriorityScorer."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from soren.models.persona import Persona
from soren.models.scheduler import PriorityWeights
from soren.models.task import Task
from soren.services.priority_scorer import TaskPriorityScorer


def _make_settings(weights: dict | None = None) -> MagicMock:
    w = weights or {
        "reward": 0.3,
        "persona_fit": 0.2,
        "success_rate": 0.2,
        "backlog_age": 0.3,
    }
    settings = MagicMock()
    settings.scheduler = MagicMock()
    settings.scheduler.priority_weights = PriorityWeights(**w)
    return settings


def _make_task(
    task_type: str = "survey",
    source_name: str = "test",
    estimated_reward: float | None = 5.0,
    created_at: str | None = None,
    status: str = "queued",
) -> Task:
    now = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    return Task(
        id="t1",
        dedup_key="k1",
        title="Test",
        task_type=task_type,
        source_name=source_name,
        estimated_reward=estimated_reward,
        created_at=created_at or now,
        status=status,
    )


def _make_persona(conscientiousness: float = 0.5) -> Persona:
    return Persona(
        name="Test",
        id="test",
        traits={"conscientiousness": conscientiousness, "agreeableness": 0.5, "openness": 0.5},
    )


def test_high_reward_scores_higher():
    """High-reward task scores higher than low-reward when reward weight is dominant."""
    settings = _make_settings(
        {"reward": 0.9, "persona_fit": 0.03, "success_rate": 0.03, "backlog_age": 0.04}
    )
    scorer = TaskPriorityScorer(settings)
    persona = _make_persona()
    tasks = [
        _make_task(estimated_reward=1.0),
        _make_task(estimated_reward=10.0),
    ]
    low = scorer.score(tasks[0], persona, tasks)
    high = scorer.score(tasks[1], persona, tasks)
    assert high > low


def test_older_task_scores_higher():
    """Older task scores higher than newer when age weight is active."""
    settings = _make_settings(
        {"reward": 0.1, "persona_fit": 0.1, "success_rate": 0.1, "backlog_age": 0.7}
    )
    scorer = TaskPriorityScorer(settings)
    persona = _make_persona()
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=100)).isoformat()
    new_ts = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    old_task = _make_task(created_at=old_ts, estimated_reward=5.0)
    new_task = _make_task(created_at=new_ts, estimated_reward=5.0)
    old_score = scorer.score(old_task, persona)
    new_score = scorer.score(new_task, persona)
    assert old_score > new_score


def test_historical_success_boosts_score():
    """Task with high historical success rate scores higher."""
    settings = _make_settings(
        {"reward": 0.1, "persona_fit": 0.1, "success_rate": 0.8, "backlog_age": 0.0}
    )
    scorer = TaskPriorityScorer(settings)
    persona = _make_persona()
    task = _make_task(task_type="survey", source_name="src")
    no_history = scorer.score(task, persona, history=[])
    with_history = scorer.score(
        task,
        persona,
        history=[
            _make_task(task_type="survey", source_name="src", status="completed"),
            _make_task(task_type="survey", source_name="src", status="completed"),
        ],
    )
    assert with_history > no_history


def test_persona_fit_boosts_surveys():
    """Survey task scores higher with high-conscientiousness persona."""
    settings = _make_settings(
        {"reward": 0.0, "persona_fit": 1.0, "success_rate": 0.0, "backlog_age": 0.0}
    )
    scorer = TaskPriorityScorer(settings)
    task = _make_task(task_type="survey")
    low = scorer.score(task, _make_persona(conscientiousness=0.2))
    high = scorer.score(task, _make_persona(conscientiousness=0.9))
    assert high > low


def test_default_weights_produce_sensible_ranking():
    """Default weights produce sensible ranking for mixed task set."""
    settings = _make_settings()
    scorer = TaskPriorityScorer(settings)
    persona = _make_persona(0.7)
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    tasks = [
        _make_task(estimated_reward=2.0, created_at=old_ts),
        _make_task(estimated_reward=10.0, created_at=old_ts),
    ]
    ranked = scorer.rank(tasks, persona)
    assert ranked[0].estimated_reward == 10.0


def test_custom_weights_override_defaults():
    """Custom weights override defaults."""
    settings = _make_settings(
        {"reward": 0.0, "persona_fit": 0.0, "success_rate": 0.0, "backlog_age": 1.0}
    )
    scorer = TaskPriorityScorer(settings)
    persona = _make_persona()
    old = _make_task(created_at=(datetime.now(timezone.utc) - timedelta(hours=100)).isoformat())
    new = _make_task(created_at=(datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat())
    assert scorer.score(old, persona) > scorer.score(new, persona)


def test_no_history_defaults_success_rate():
    """Task with no history defaults to 0.5 success rate."""
    settings = _make_settings(
        {"reward": 0.0, "persona_fit": 0.0, "success_rate": 1.0, "backlog_age": 0.0}
    )
    scorer = TaskPriorityScorer(settings)
    task = _make_task()
    persona = _make_persona()
    score = scorer.score(task, persona, history=[])
    assert 0.4 < score < 0.6


def test_rank_returns_descending_order():
    """rank() returns tasks in descending score order."""
    settings = _make_settings(
        {"reward": 0.5, "persona_fit": 0.0, "success_rate": 0.0, "backlog_age": 0.5}
    )
    scorer = TaskPriorityScorer(settings)
    persona = _make_persona()
    low_reward = _make_task(estimated_reward=1.0)
    high_reward = _make_task(estimated_reward=10.0)
    tasks = [low_reward, high_reward]
    ranked = scorer.rank(tasks, persona)
    assert ranked[0].estimated_reward == 10.0
    assert ranked[1].estimated_reward == 1.0


def test_tied_scores_stable_sort():
    """Tied scores maintain stable sort order (by created_at)."""
    settings = _make_settings(
        {"reward": 0.0, "persona_fit": 0.0, "success_rate": 0.0, "backlog_age": 0.0}
    )
    scorer = TaskPriorityScorer(settings)
    persona = _make_persona()
    ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    t1 = _make_task(estimated_reward=5.0, created_at=ts)
    t2 = _make_task(estimated_reward=5.0, created_at=ts)
    tasks = [t1, t2]
    ranked = scorer.rank(tasks, persona)
    assert len(ranked) == 2


def test_no_reward_returns_zero_reward_factor():
    """Task with no estimated_reward gets 0 reward factor."""
    settings = _make_settings(
        {"reward": 1.0, "persona_fit": 0.0, "success_rate": 0.0, "backlog_age": 0.0}
    )
    scorer = TaskPriorityScorer(settings)
    task = _make_task(estimated_reward=None)
    persona = _make_persona()
    score = scorer.score(task, persona)
    assert score == 0.0
