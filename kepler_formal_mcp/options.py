"""JSON-compatible option choices shared by MCP schemas and YAML validation.

The API coverage tests compare these choices with the installed Kepler enums.
Keeping native imports out of the server protects the MCP stdio channel.
"""

from typing import Literal


Mode = Literal["lec", "sec"]
Solver = Literal["kissat", "cadical", "glucose"]
SecEngine = Literal["pdr", "k_induction", "imc"]
SecEncoding = Literal["dual_rail_steady", "binary"]
LogLevel = Literal["info", "debug"]
