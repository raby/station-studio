import base from './playwright.config';
import { defineConfig } from '@playwright/test';
export default defineConfig({
  ...base,
  projects: [{ name: 'chromium', use: { ...(base.projects![0].use), launchOptions: { executablePath: '/opt/pw-browsers/chromium_headless_shell-1243/chrome-headless-shell-linux64/chrome-headless-shell' } } }],
});
