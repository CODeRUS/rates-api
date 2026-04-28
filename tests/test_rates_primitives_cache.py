# -*- coding: utf-8 -*-
from __future__ import annotations

import time
import unittest

import rates_unified_cache as ucc


class TestPrimAndL2Deps(unittest.TestCase):
    def test_l2_deps_prim_version_mismatch(self) -> None:
        doc = ucc._empty_doc()
        ucc.prim_set(doc, "prim:test:v1", {"x": 1}, ttl_sec=3600)
        ver = doc["prim"]["prim:test:v1"]["version"]
        self.assertTrue(ucc.l2_deps_match(doc, {"prim:test:v1": ver}))
        self.assertFalse(ucc.l2_deps_match(doc, {"prim:test:v1": ver + 1}))

    def test_l2_deps_prim_expired_ttl_invalidates(self) -> None:
        doc = ucc._empty_doc()
        ucc.prim_set(doc, "prim:test:v1", {"x": 1}, ttl_sec=1)
        ver = doc["prim"]["prim:test:v1"]["version"]
        doc["prim"]["prim:test:v1"]["saved_unix"] = time.time() - 10
        self.assertFalse(ucc.l2_deps_match(doc, {"prim:test:v1": ver}))

    def test_l2_deps_l1_expired_ttl_invalidates(self) -> None:
        doc = ucc._empty_doc()
        ucc.l1_set(doc, "rs:forex:abc", [], ttl_sec=60)
        ver = doc["l1"]["rs:forex:abc"]["version"]
        doc["l1"]["rs:forex:abc"]["saved_unix"] = time.time() - 3600
        self.assertFalse(ucc.l2_deps_match(doc, {"rs:forex:abc": ver}))

    def test_l2_deps_orphan_prim_invalidates(self) -> None:
        doc = ucc._empty_doc()
        ucc.prim_set(doc, "prim:orphan:v1", {"x": 1}, ttl_sec=3600)
        ucc.l1_set(doc, "rs:forex:abc", [], ttl_sec=3600)
        ver_p = doc["prim"]["prim:orphan:v1"]["version"]
        ver_rs = doc["l1"]["rs:forex:abc"]["version"]
        self.assertFalse(
            ucc.l2_deps_match_with_orphan_prims(
                doc, {"rs:forex:abc": ver_rs}, ("prim:orphan:v1",)
            )
        )
        self.assertTrue(
            ucc.l2_deps_match_with_orphan_prims(
                doc,
                {"rs:forex:abc": ver_rs, "prim:orphan:v1": ver_p},
                ("prim:orphan:v1",),
            )
        )


if __name__ == "__main__":
    unittest.main()
