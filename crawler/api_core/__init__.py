"""Service layer shared between CLI and MCP.

api_core/ is a thin layer of pure async functions that receive IRepository
and other Protocol dependencies as arguments (DI via function parameters).
CLI commands and MCP tools (E4) are thin wrappers over these functions.
"""
