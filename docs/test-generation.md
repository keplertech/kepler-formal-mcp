# A small equivalence example

Create these three files in a design directory. They use only Verilog assignments, so no Liberty files are needed.

`reference.v`:

```verilog
module reference(input a, output y);
  assign y = a;
endmodule
```

`equivalent.v`:

```verilog
module candidate(input a, output y);
  wire connection;
  assign connection = a;
  assign y = connection;
endmodule
```

`different.v`:

```verilog
module candidate(input a, output y);
  assign y = 1'b0;
endmodule
```

After [configuring your MCP client](instructions-claude.md), call `create_yaml_and_run_kepler_formal` with:

```json
{
  "input_paths": ["/absolute/path/reference.v", "/absolute/path/equivalent.v"],
  "liberty_files": [],
  "allowed_output_dir": "/absolute/path/verification-output",
  "yaml_output_path": "equivalent.yaml",
  "verification": "lec"
}
```

The result should have `status: "success"` and `verdict: "equivalent"`. Change the second input to `different.v` and the YAML output name to `different.yaml`: execution should still succeed, with `verdict: "different"`.

You can also create a YAML file beside the designs and pass its path to `run_kepler_formal_yaml`:

```yaml
format: verilog
input_paths:
  - reference.v
  - equivalent.v
liberty_files: []
verification: lec
solver: kissat
cnf_export: false
```

Pass `allowed_output_dir` to choose a writable log directory. Relative input and library paths in an existing YAML file are resolved from that file's directory. The input YAML is not rewritten.

For cell-based netlists, supply the common `.lib` files in `liberty_files`. For sequential checking, use `verification: sec` with the supported `max_k`, `sec_engine`, and `sec_encoding` settings. Inspect the returned verdict and counterexample information: `inconclusive` does not mean equivalent, and a reported counterexample does not enumerate every possible difference.
