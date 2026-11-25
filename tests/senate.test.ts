// Unit tests for the pure helpers of src/senate.ts (no network).

import { test } from "node:test";
import assert from "node:assert/strict";
import { parentNumberFromNumber } from "../src/senate.ts";

test("parentNumberFromNumber: subemendamento X/N -> X", () => {
  assert.equal(parentNumberFromNumber("2.0.1000/14", "E"), "2.0.1000");
  assert.equal(parentNumberFromNumber("1.100/1", "E"), "1.100");
});

test("parentNumberFromNumber: emendamento base -> undefined", () => {
  assert.equal(parentNumberFromNumber("2.14", "E"), undefined);
  assert.equal(parentNumberFromNumber("G2.101", "G"), undefined);
});

test("parentNumberFromNumber: gli o.d.g. hanno numeri con slash nativi, non sono subemendamenti", () => {
  assert.equal(parentNumberFromNumber("G/912/1/5", "G"), undefined);
});
