import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "tests/ui/specs",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI
    ? [["line"], ["html", { open: "never" }]]
    : [["list"], ["html", { open: "never" }]],
  expect: { timeout: 5_000 },
  use: {
    baseURL: "http://127.0.0.1:4173",
    locale: "de-DE",
    timezoneId: "Europe/Berlin",
    colorScheme: "light",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: {
    command: "node tests/ui/harness/server.mjs",
    url: "http://127.0.0.1:4173/tests/ui/harness/",
    reuseExistingServer: !process.env.CI,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 1000 } },
    },
    {
      name: "mobile-webkit",
      use: { ...devices["iPhone 13"] },
      testIgnore: /visual[.]spec[.]js/,
    },
  ],
  snapshotPathTemplate: "{testDir}/__screenshots__/{testFilePath}/{arg}-{projectName}{ext}",
});
