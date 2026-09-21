"""obsidian-writer: AI-agent-facing FastAPI service for an Obsidian vault.

The service is intentionally small. The vault filesystem is the source of
truth; this service is a thin, safe write surface over it. Structure is
learned by the agent and cached in `.obsidian-map.yaml` at the vault root.
"""

__version__ = "0.1.0"
