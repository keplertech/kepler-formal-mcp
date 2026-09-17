"""Verify persistent workers and bridges to caller-owned NajaEDA designs."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kepler_formal_mcp import server
from kepler_formal_mcp.session_bridge import SessionBridge


PASS_THROUGH = "module top(input a, output y); assign y = a; endmodule\n"
CONSTANT_OUTPUT = "module top(input a, output y); assign y = 1'b0; endmodule\n"


class SessionFixture(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="kepler sessions ")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.reference = self.root / "reference.v"
        self.candidate = self.root / "candidate.v"
        self.reference.write_text(PASS_THROUGH, encoding="utf-8")
        self.candidate.write_text(CONSTANT_OUTPUT, encoding="utf-8")

    def call(self, tool, **arguments):
        return json.loads(getattr(server, tool)(**arguments))

    def open(self, name="results"):
        session = self.call("open_session", allowed_output_dir=str(self.root / name))
        self.assert_success(session)
        self.addCleanup(server.close_session, session_id=session["session_id"])
        return session

    def attach(self, bridge):
        session = self.call("attach_session", connection_file=str(bridge.connection_file))
        self.assert_success(session)
        self.addCleanup(server.close_session, session_id=session["session_id"])
        return session

    def load(self, session_id=None, **arguments):
        return self.call(
            "load_designs",
            input_paths=[str(self.reference), str(self.candidate)],
            liberty_files=[],
            session_id=session_id,
            timeout_seconds=30,
            **arguments,
        )

    def verify(self, session_id=None, **arguments):
        return self.call("verify_session", session_id=session_id, timeout_seconds=30, **arguments)

    def assert_success(self, result):
        self.assertEqual(result["status"], "success", result)

    def assert_verdict(self, result, expected):
        self.assert_success(result)
        self.assertEqual(result["verdict"], expected, result)
        self.assertEqual(result["verification_result"]["status"], expected, result)


class ManagedSessionTest(SessionFixture):
    def test_loaded_designs_survive_source_deletion_and_repeated_verification(self):
        session = self.open()
        self.assertEqual(session["kind"], "managed")
        self.assertNotEqual(session["pid"], os.getpid())
        self.assert_success(self.load(session["session_id"]))
        self.reference.unlink()
        self.candidate.unlink()
        for options in ({}, {"solver": "cadical"}, {
            "verification": "sec", "sec_engine": "pdr", "sec_encoding": "binary", "max_k": 2,
        }):
            with self.subTest(options=options):
                result = self.verify(session["session_id"], **options)
                self.assert_verdict(result, "different")
                self.assertEqual(result["pid"], session["pid"])
                self.assertEqual(result["session_id"], session["session_id"])

    def test_multiple_sessions_are_independent_and_can_be_selected(self):
        different = self.open("different")
        self.assert_success(self.load())
        self.candidate.write_text(PASS_THROUGH, encoding="utf-8")
        equivalent = self.open("equivalent")
        self.assert_success(self.load(names=["golden", "revised"]))
        self.assertNotEqual(different["pid"], equivalent["pid"])
        self.assertNotEqual(different["session_id"], equivalent["session_id"])
        self.assert_verdict(self.verify(design1="golden", design2="revised"), "equivalent")
        self.assert_success(self.call("set_session", session_id=different["session_id"]))
        self.assert_verdict(self.verify(), "different")
        self.assert_verdict(self.verify(
            equivalent["session_id"], design1="golden", design2="revised",
        ), "equivalent")
        listing = self.call("list_sessions")
        self.assertEqual(listing["active_session_id"], different["session_id"])
        self.assertTrue(
            {different["session_id"], equivalent["session_id"]}
            <= {item["session_id"] for item in listing["sessions"]}
        )

    def test_closed_and_unknown_sessions_are_rejected(self):
        session = self.open()
        closed = self.call("close_session", session_id=session["session_id"])
        self.assert_success(closed)
        self.assertEqual(closed["state"], "closed")
        listing = self.call("list_sessions")
        self.assertNotIn(session["session_id"], {item["session_id"] for item in listing["sessions"]})
        for session_id in (session["session_id"], "not-a-session"):
            with self.subTest(session_id=session_id):
                self.assertEqual(self.verify(session_id)["status"], "error")
                self.assertEqual(self.call("set_session", session_id=session_id)["status"], "error")

    def test_invalid_load_does_not_discard_previously_loaded_designs(self):
        session = self.open()
        self.assert_success(self.load())
        invalid = self.call("load_designs", input_paths=[str(self.reference)], session_id=session["session_id"])
        self.assertEqual(invalid["status"], "error", invalid)
        self.assert_verdict(self.verify(), "different")
        missing = self.verify(design1="not-registered")
        self.assertEqual(missing["status"], "error", missing)
        self.assert_verdict(self.verify(), "different")

    def test_select_cannot_reactivate_a_session_closed_during_inspection(self):
        session = self.open()
        manager = server.session_tools.manager
        original_call = manager.call

        def close_after_inspection(*args, **kwargs):
            response = original_call(*args, **kwargs)
            manager.close(session["session_id"])
            return response

        with patch.object(manager, "call", side_effect=close_after_inspection):
            result = self.call("set_session", session_id=session["session_id"])
        self.assertEqual(result["status"], "error", result)
        listing = self.call("list_sessions")
        self.assertIsNone(listing["active_session_id"])
        self.assertNotIn(session["session_id"], {item["session_id"] for item in listing["sessions"]})

    def test_session_logs_stay_in_selected_directory(self):
        self.open()
        self.assert_success(self.load())
        rejected = self.verify(log_file_name=str(self.root / "outside.log"))
        self.assertEqual(rejected["status"], "error", rejected)
        self.assertFalse((self.root / "outside.log").exists())
        result = self.verify(log_file_name="inside.log")
        self.assert_verdict(result, "different")
        self.assertEqual(Path(result["generated_log_file"]), self.root / "results" / "inside.log")

    def test_timed_out_managed_session_is_invalidated(self):
        session = self.open()
        self.assert_success(self.load())
        with patch("kepler_formal_mcp.session_manager._request", side_effect=TimeoutError("timed out")):
            result = self.verify()
        self.assertEqual(result["status"], "error", result)
        self.assertTrue(result["session_invalidated"], result)
        listing = self.call("list_sessions")
        self.assertNotIn(session["session_id"], {item["session_id"] for item in listing["sessions"]})
        self.assertEqual(self.verify(session["session_id"])["status"], "error")

    def test_session_tools_are_exposed_to_mcp_clients(self):
        names = {tool.name for tool in asyncio.run(server.app.list_tools())}
        self.assertTrue({
            "open_session", "set_session", "list_sessions", "load_designs",
            "verify_session", "get_session_reports", "close_session", "attach_session",
        } <= names)


class AttachedSessionTest(SessionFixture):
    def setUp(self):
        super().setUp()
        from najaeda import naja

        self.assertIsNone(naja.NLUniverse.get(), "These tests require a fresh caller-owned universe")
        self.universe = naja.NLUniverse.create()
        self.addCleanup(self.universe.destroy)
        self.designs = []
        for path in (self.reference, self.candidate):
            database = naja.NLDB.create(self.universe)
            database.loadVerilog([str(path)])
            self.designs.append(database.getTopDesign())

    def bridge(self):
        bridge = SessionBridge(output_dir=self.root / "results")
        self.addCleanup(bridge.close)
        return bridge.start()

    def test_attached_designs_are_live_and_detaching_preserves_caller_ownership(self):
        from kepler_formal import VerificationOptions, from_najaeda, verify_designs
        from najaeda import naja, netlist

        reference, candidate = self.designs
        bridge = self.bridge()
        self.universe.setTopDesign(reference)
        bridge.register_design("reference", netlist.get_top())
        bridge.register_design("raw_reference", reference)
        self.universe.setTopDesign(candidate)
        bridge.register_design("candidate", from_najaeda(candidate))
        session = self.attach(bridge)
        self.assertEqual(session["kind"], "attached")
        self.assertEqual(session["pid"], os.getpid())
        self.reference.unlink()
        self.candidate.unlink()
        self.assert_verdict(self.verify(), "different")
        self.assert_verdict(self.verify(design1="raw_reference"), "different")

        with bridge.lock:
            candidate.getScalarTerm("y").setNet(candidate.getScalarTerm("a").getNet())
        result = self.verify()
        self.assert_verdict(result, "equivalent")
        self.assertEqual(result["pid"], os.getpid())
        detached = self.call("close_session", session_id=session["session_id"])
        self.assert_success(detached)
        self.assertEqual(detached["state"], "detached")
        self.assertIsNotNone(naja.NLUniverse.get())
        self.assertEqual(candidate.getName(), "top")
        # Detaching did not shut down the caller's bridge: it can be reattached.
        reattached = self.attach(bridge)
        self.assert_verdict(self.verify(reattached["session_id"]), "equivalent")
        self.call("close_session", session_id=reattached["session_id"])
        bridge.close()
        self.assertIsNotNone(naja.NLUniverse.get())
        native = verify_designs(reference, candidate, options=VerificationOptions(
            log_file=self.root / "still-owned.log",
        ))
        self.assertEqual(native.status.value, "equivalent")

    def test_attached_sessions_preserve_reports_without_process_relative_writes(self):
        bridge = self.bridge()
        bridge.register_design("reference", self.designs[0])
        bridge.register_design("candidate", self.designs[1])
        self.attach(bridge)
        original_cwd = Path.cwd()
        before = {p: p.read_bytes() for p in original_cwd.glob("skipped*pos.txt")}
        result = self.verify(verification="sec", report_skipped_outputs=True)
        self.assert_verdict(result, "different")
        self.assertEqual(Path.cwd(), original_cwd)
        self.assertEqual(before, {p: p.read_bytes() for p in original_cwd.glob("skipped*pos.txt")})
        self.assertEqual(result["report_format"], "structured-v1")
        report = json.loads(result["reports"]["verification-result.json"])
        self.assertEqual(report, result["verification_result"])
        path = Path(result["report_paths"]["verification-result.json"])
        self.assertTrue(path.is_relative_to(self.root / "results"))
        self.assertEqual(json.loads(path.read_text()), report)
        fetched = self.call("get_session_reports", report_id=result["report_id"])
        self.assertEqual(fetched["verification_result"], report)
        next_result = self.verify(verification="sec", report_skipped_outputs=True)
        self.assertNotEqual(next_result["report_id"], result["report_id"])
        self.assertEqual(self.call("get_session_reports", report_id=result["report_id"])["status"], "error")
        self.assertEqual(json.loads(path.read_text()), report)

    def test_session_reports_require_a_completed_verification(self):
        self.attach(self.bridge())
        result = self.call("get_session_reports")
        self.assertEqual(result["status"], "error")

    def test_attached_report_preserves_actual_skipped_and_unproven_fields(self):
        bridge = self.bridge()
        bridge.register_design("reference", self.designs[0])
        bridge.register_design("candidate", self.designs[1])
        self.attach(bridge)
        native = {"status": "partially_proved", "exit_code": 2, "verification": "sec",
                  "total_outputs": 3, "covered_outputs": 2, "proven_outputs": 1,
                  "unproven_outputs": ["pending"],
                  "skipped_observed_outputs": ["floating: no drivers"], "reason": "incomplete"}
        with patch("kepler_formal_mcp.session_backend.verify_loaded", return_value=native) as verify:
            result = self.verify(verification="sec", report_skipped_outputs=True)
        self.assertFalse(verify.call_args.args[2]["report_skipped_outputs"])
        self.assertEqual(json.loads(result["reports"]["verification-result.json"]), native)
        self.assertEqual(self.call("get_session_reports")["verification_result"], native)

    def test_attached_native_sec_reports_skipped_cones_without_dumping(self):
        from najaeda import naja

        liberty = self.root / "cells.lib"
        liberty.write_text('''library(test) {
          cell(BUF) {
            pin(A) { direction: input; }
            pin(Y) { direction: output; function: "A"; }
          }
        }''')
        paths = [self.root / "floating.v", self.root / "driven.v"]
        paths[0].write_text('''module top(input a, output good, output floating);
          wire undriven;
          BUF g(.A(a), .Y(good));
          BUF f(.A(undriven), .Y(floating));
        endmodule''')
        paths[1].write_text('''module top(input a, output good, output floating);
          BUF g(.A(a), .Y(good));
          BUF f(.A(a), .Y(floating));
        endmodule''')
        bridge = self.bridge()
        for name, path in zip(("reference", "candidate"), paths):
            database = naja.NLDB.create(self.universe)
            database.loadLibertyPrimitives([str(liberty)])
            database.loadVerilog([str(path)])
            bridge.register_design(name, database.getTopDesign())
        self.attach(bridge)
        result = self.verify(verification="sec", report_skipped_outputs=True)
        proof = result["verification_result"]
        self.assertTrue(proof["skipped_observed_outputs"], result)
        self.assertIn("floating", str(proof["skipped_observed_outputs"]))
        self.assertEqual(json.loads(result["reports"]["verification-result.json"]), proof)
        fetched = self.call("get_session_reports", report_id=result["report_id"])
        self.assertEqual(fetched["verification_result"], proof)

    def test_attached_timeout_does_not_kill_or_invalidate_callers_process(self):
        bridge = self.bridge()
        bridge.register_design("reference", self.designs[0])
        bridge.register_design("candidate", self.designs[1])
        session = self.attach(bridge)
        with patch("kepler_formal_mcp.session_manager._request", side_effect=TimeoutError("timed out")):
            result = self.verify()
        self.assertEqual(result["status"], "error", result)
        self.assertFalse(result["session_invalidated"], result)
        self.assertTrue(result["may_still_be_running"], result)
        listing = self.call("list_sessions")
        self.assertIn(session["session_id"], {item["session_id"] for item in listing["sessions"]})
        self.assert_verdict(self.verify(session["session_id"]), "different")
        self.assertEqual(self.designs[0].getName(), "top")

    def test_wrong_token_cannot_attach_to_a_live_bridge(self):
        bridge = self.bridge()
        bridge.register_design("reference", self.designs[0])
        bridge.register_design("candidate", self.designs[1])
        descriptor = json.loads(Path(bridge.connection_file).read_text(encoding="utf-8"))
        descriptor["token"] = "0" * 64 if descriptor["token"] != "0" * 64 else "1" * 64
        invalid = self.root / "wrong-token.json"
        invalid.write_text(json.dumps(descriptor), encoding="utf-8")
        invalid.chmod(0o600)
        rejected = self.call("attach_session", connection_file=str(invalid))
        self.assertEqual(rejected["status"], "error", rejected)
        self.assertIn("auth", json.dumps(rejected).lower())
        self.attach(bridge)
        self.assert_verdict(self.verify(), "different")

    def test_caller_edit_lock_prevents_overlapping_verification(self):
        bridge = self.bridge()
        bridge.register_design("reference", self.designs[0])
        bridge.register_design("candidate", self.designs[1])
        self.attach(bridge)
        with bridge.lock:
            busy = self.verify()
        self.assertEqual(busy["status"], "error", busy)
        self.assertIn("busy", json.dumps(busy).lower())
        self.assert_verdict(self.verify(), "different")

    def test_bad_connection_descriptor_is_a_structured_error(self):
        descriptor = self.root / "invalid.json"
        for text in ("not json", "{}", "[]"):
            with self.subTest(text=text):
                descriptor.write_text(text, encoding="utf-8")
                result = self.call("attach_session", connection_file=str(descriptor))
                self.assertEqual(result["status"], "error", result)


if __name__ == "__main__":
    unittest.main()
