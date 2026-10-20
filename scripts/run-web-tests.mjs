import { spawnSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { mkdirSync, readFileSync, realpathSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

export function summarizeWebTestReport(report) {
  if (!report || !Array.isArray(report.testResults)) {
    throw new Error("Vitest did not produce a valid test-results report.");
  }
  const lines = [];
  for (const file of report.testResults) {
    const failed = (file.assertionResults ?? []).filter((test) => test.status === "failed");
    if (file.status !== "failed" && failed.length === 0) continue;
    // Vitest reports Windows paths even when a report is inspected elsewhere.
    const filename = String(file.name ?? "Unknown test file").replaceAll("\\", "/").split("/apps/web/").at(-1);
    if (failed.length === 0) {
      lines.push(`FAIL ${filename}: ${firstErrorLine(file.message || "Test file could not run.")}`);
    }
    for (const test of failed) {
      lines.push(`FAIL ${filename}: ${test.fullName || test.title || "Unnamed test"}`);
      const detail = (test.failureMessages ?? []).find((message) => String(message).trim());
      if (detail) lines.push(`  ${firstErrorLine(detail)}`);
    }
  }
  const observedTests = report.testResults.reduce((count, file) => count + (file.assertionResults?.length ?? 0), 0);
  const passed = report.success === true && observedTests > 0 && report.numTotalTests === observedTests &&
    report.numFailedTests === 0 && report.numFailedTestSuites === 0 && lines.length === 0;
  if (!passed && lines.length === 0) {
    lines.push("Web tests did not complete successfully. Inspect the JSON report and build log for worker or collection errors.");
  }
  return { passed, lines };
}

function firstErrorLine(value) {
  return String(value).replace(/\u001b\[[0-9;]*m/g, "").split(/\r?\n/).find((line) => line.trim())?.slice(0, 350) ?? "Unknown test error";
}

export function runWebTests(reportPath) {
  const absoluteReport = path.resolve(reportPath);
  mkdirSync(path.dirname(absoluteReport), { recursive: true });
  // Direct Node invocation works on Windows without cmd.exe quoting or shell
  // execution. Keep file isolation, assertions, and failures enabled. Two
  // workers bound jsdom startup/memory pressure on personal Windows PCs.
  const result = spawnSync(process.execPath, [
    path.join(repositoryRoot, "node_modules/vitest/vitest.mjs"),
    "run", "--maxWorkers=2", "--reporter=default", "--reporter=json",
    `--outputFile.json=${absoluteReport}`,
  ], { cwd: path.join(repositoryRoot, "apps/web"), stdio: "inherit", env: process.env });

  let summary;
  try {
    summary = summarizeWebTestReport(JSON.parse(readFileSync(absoluteReport, "utf8")));
  } catch (error) {
    summary = { passed: false, lines: [`Web test report unavailable: ${error.message}`] };
  }
  if (result.error) summary.lines.push(`Could not start Vitest: ${result.error.message}`);
  if (result.signal) summary.lines.push(`Vitest was interrupted by ${result.signal}.`);
  const succeeded = result.status === 0 && !result.error && summary.passed;
  console.log(`\nWeb test report: ${absoluteReport}`);
  if (!succeeded) {
    if (summary.lines.length === 0) summary.lines.push(`Vitest exited with code ${result.status ?? "unknown"}.`);
    const text = ["WEB TEST FAILURE — exact test(s)", ...summary.lines, `Full details: ${absoluteReport}`].join("\n");
    writeFileSync(`${absoluteReport}.failure.txt`, `${text}\n`, "utf8");
    console.error(`\n${text}`);
  }
  return succeeded ? 0 : 1;
}

// Resolve symlinks and Windows path casing before deciding whether this is the
// entry point; a mismatched spelling must not quietly skip the entire test run.
function canonicalFile(value) {
  const resolved = realpathSync(value);
  return process.platform === "win32" ? resolved.toLowerCase() : resolved;
}

if (process.argv[1] && canonicalFile(process.argv[1]) === canonicalFile(fileURLToPath(import.meta.url))) {
  const reportPath = process.argv[2] ?? path.join(repositoryRoot, "logs", `web-tests-${randomUUID()}.json`);
  process.exitCode = runWebTests(reportPath);
}
