"""PULSE activation subsystem — stateless Member orchestration.

Entry point: ``orchestrator.pulse()`` gathers active Members, applies
pre-flight gates (load, frequency, business hours), sorts by dependency,
and calls a provided ``run_member`` callback for each eligible Member.
"""
