"""
AIMFP Helper Functions - Tracking Audit

Verifies project.db against the source it describes and repairs drift in bulk.
Tools live in their own modules (registry.py maps each one); this package
marker re-exports nothing so importing one audit tool never loads the rest.
"""
