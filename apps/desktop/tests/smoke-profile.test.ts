import assert from "node:assert/strict";
import test from "node:test";
import { resolveSmokeTestConfiguration } from "../src/smoke-profile";

const nonce = "windows-electron-0123456789abcdef0123456789abcdef";
const smokeArguments = [`--edgeos-smoke-test=${nonce}`];

test("ordinary launches ignore the smoke profile environment override", () => {
  assert.equal(resolveSmokeTestConfiguration([], "C:\\Smoke Profile\\EDGEOS", "win32"), null);
  assert.equal(resolveSmokeTestConfiguration([], "\0invalid", "win32"), null);
  for (const invalidNonce of ["", "short", "n".repeat(101), "n".repeat(20) + "/"]) {
    assert.equal(resolveSmokeTestConfiguration(
      [`--edgeos-smoke-test=${invalidNonce}`], "C:\\Smoke Profile\\EDGEOS", "win32",
    ), null);
  }
});

test("smoke profiles normalize fully qualified Windows paths and preserve spaces", () => {
  assert.deepEqual(resolveSmokeTestConfiguration(
    smokeArguments, "C:/Acceptance Profile/temporary/../EDGEOS", "win32",
  ), { nonce, userData: "C:\\Acceptance Profile\\EDGEOS" });
  assert.deepEqual(resolveSmokeTestConfiguration(
    smokeArguments, "\\\\server\\share\\Acceptance Profile\\EDGEOS", "win32",
  ), { nonce, userData: "\\\\server\\share\\Acceptance Profile\\EDGEOS" });
});

test("valid smoke requests fail closed for missing or invalid Windows profiles", () => {
  for (const invalidPath of [
    undefined, "", " ", ".", "..", "relative\\EDGEOS", "C:EDGEOS", "\\EDGEOS", "/EDGEOS",
    "C:\\", "C:/", "C:\\EDGEOS\\..", "\\\\server", "\\\\server\\share",
    "\\\\server\\share\\", "\\\\server\\share\\EDGEOS\\..", "\\\\?\\C:\\EDGEOS",
    "\\\\.\\C:\\EDGEOS", "C:\\invalid\0path", "C:\\invalid\npath",
    "C:\\invalid?path", "C:\\invalid:path", "C:\\   ", "C:\\...",
    "C:\\profile.\\EDGEOS", "C:\\NUL", "C:\\CON.txt", "\\\\server\\share\\...",
  ]) {
    assert.throws(
      () => resolveSmokeTestConfiguration(smokeArguments, invalidPath, "win32"),
      /absolute, non-root directory/u,
      `Unexpectedly accepted smoke profile: ${JSON.stringify(invalidPath)}`,
    );
  }
});

test("smoke profiles use the target platform's absolute path rules", () => {
  assert.deepEqual(resolveSmokeTestConfiguration(
    smokeArguments, "/tmp/acceptance profile/temporary/../EDGEOS", "linux",
  ), { nonce, userData: "/tmp/acceptance profile/EDGEOS" });
  for (const invalidPath of [undefined, "", "relative/EDGEOS", "/", "/tmp/..", "C:\\EDGEOS"]) {
    assert.throws(
      () => resolveSmokeTestConfiguration(smokeArguments, invalidPath, "linux"),
      /absolute, non-root directory/u,
    );
  }
});
