// Test setup: a fresh document for each test, a fake API (test/server.ts) instead of the network, and an
// EventSource stand-in the tests drive by hand.

import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";
import { location_, setCsrf } from "../src/api";
import { FakeEventSource } from "./server";

beforeEach(() => {
  setCsrf("test-token");
  document.documentElement.dataset.theme = "dark";
  document.head.innerHTML = '<meta name="color-scheme" content="dark">';
  window.history.replaceState({}, "", "/");
  vi.stubGlobal("EventSource", FakeEventSource);
  vi.spyOn(location_, "assign").mockImplementation(() => undefined);
  window.scrollTo = () => undefined;
  Element.prototype.scrollIntoView = function scrollIntoView() {};
});

afterEach(() => {
  cleanup();
  FakeEventSource.instances = [];
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
