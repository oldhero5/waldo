"""Redis delivery window covers configured limits; solo runtime bounds are unverified."""

from lib.tasks import app


def test_redis_visibility_window_exceeds_all_configured_task_hard_limits():
    timeouts = [
        app.conf.broker_transport_options.get("visibility_timeout"),
        app.conf.result_backend_transport_options.get("visibility_timeout"),
        app.conf.get("visibility_timeout"),
    ]
    assert timeouts == [25 * 3600] * 3
    assert all(type(timeout) is int for timeout in timeouts)
    limits = [task.time_limit for name, task in app.tasks.items() if name.startswith("waldo.") and task.time_limit]
    assert max(limits) == app.tasks["waldo.train_model"].time_limit == 24 * 3600 + 300
    assert timeouts[0] > max(limits)
    assert app.conf.task_acks_late is True
