"""AGENTIC STAR Marketplace entrypoint for LOG-C2-051."""

from shared.bootstrap.marketplace_app import run_agent_marketplace
from src.graph.graph import Graph


if __name__ == "__main__":
    run_agent_marketplace(Graph, agent_name="LOG-C2-051", namespace="agent1000")
