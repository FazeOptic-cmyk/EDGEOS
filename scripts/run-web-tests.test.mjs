import assert from "node:assert/strict";
import test from "node:test";
import { summarizeWebTestReport } from "./run-web-tests.mjs";

const passing = {
  success: true, numTotalTests: 1, numFailedTests: 0, numFailedTestSuites: 0,
  testResults: [{ name: "/source/apps/web/example.test.ts", status: "passed", assertionResults: [{ status: "passed" }] }],
};

test("accepts a complete passing run", () => {
  assert.deepEqual(summarizeWebTestReport(passing), { passed: true, lines: [] });
});

test("reports the failed title and first error without burying it in a DOM dump", () => {
  const summary = summarizeWebTestReport({
    ...passing, success: false, numFailedTests: 1, numFailedTestSuites: 1,
    testResults: [{
      name: "D:\\EDGEOS\\apps\\web\\components\\example.test.tsx", status: "failed",
      assertionResults: [{ status: "failed", fullName: "Simulator waits for a completed result", failureMessages: ["AssertionError: expected 1 call\nLong DOM dump"] }],
    }],
  });
  assert.equal(summary.passed, false);
  assert.deepEqual(summary.lines, [
    "FAIL components/example.test.tsx: Simulator waits for a completed result",
    "  AssertionError: expected 1 call",
  ]);
});

test("reports collection failures even when no assertions ran", () => {
  const summary = summarizeWebTestReport({ ...passing, success: false, testResults: [
    { name: "/source/apps/web/broken.test.ts", status: "failed", assertionResults: [], message: "Could not import dependency\nStack" },
  ] });
  assert.deepEqual(summary.lines, ["FAIL broken.test.ts: Could not import dependency"]);
  assert.equal(summary.passed, false);
});

test("never turns empty, incomplete, inconsistent, or malformed output into a pass", () => {
  assert.equal(summarizeWebTestReport({ ...passing, numTotalTests: 0 }).passed, false);
  assert.equal(summarizeWebTestReport({ ...passing, testResults: [] }).passed, false);
  assert.equal(summarizeWebTestReport({ ...passing, success: false }).passed, false);
  assert.equal(summarizeWebTestReport({ ...passing, numFailedTestSuites: 1 }).passed, false);
  assert.throws(() => summarizeWebTestReport({}), /valid test-results/);
  assert.throws(() => summarizeWebTestReport(null), /valid test-results/);
});
