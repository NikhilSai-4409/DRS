/**
 * Calibration view shell (Slice 2): two tabs over one camera-calibration page.
 *
 *   Camera Intrinsics  → IntrinsicsPanel (new ChArUco lens-calibration workflow)
 *   Pitch Calibration  → the existing CalibrationWorkspace, UNCHANGED (Slice 3 redesigns it)
 *
 * Only the visible tab's panel is active; switching tabs deactivates the other so a
 * single live-preview poll runs at a time. Role callbacks pass straight through to
 * the pitch workspace.
 */

import { CalibrationWorkspace } from "./CalibrationModal.js";
import { IntrinsicsPanel } from "./IntrinsicsPanel.js";
import { PosePanel } from "./PosePanel.js";

const TABS = [
  ["intrinsics", "Camera Intrinsics"],
  ["pose", "Camera Pose"],
  ["pitch", "Pitch Calibration (legacy)"],
];

export class CalibrationTabs {
  constructor(host, opts = {}) {
    this.host = host;
    this.opts = opts;
    this.active = false;
    this.tab = "intrinsics";
    this.intrinsics = null;
    this.pose = null;
    this.pitch = null;
  }

  async activate() {
    this.active = true;
    // A click inside the calibration view bubbles up to renderer.js's section[data-view]
    // router, which calls activate() again. Rebuilding the shell here would detach the
    // panels' hosts and blank the page, so build the shell only once and otherwise just
    // ensure the visible panel is live.
    if (!this.host.querySelector(".cal-tabs")) {
      this.render();
      await this.showTab(this.tab, true);
      return;
    }
    const panel = this.tab === "intrinsics" ? this.intrinsics
      : this.tab === "pose" ? this.pose : this.pitch;
    if (panel && !panel.active) await panel.activate();
  }

  deactivate() {
    this.active = false;
    this.intrinsics?.deactivate?.();
    this.pose?.deactivate?.();
    this.pitch?.deactivate?.();
  }

  // Legacy entry points (router used to call open/close on the workspace).
  open() { return this.activate(); }
  close() { return this.deactivate(); }

  render() {
    this.host.innerHTML = `
      <div class="cal-tabs">
        <div class="cal-tabbar" role="tablist">
          ${TABS.map(([key, label]) => `<button class="cal-tab ${key === this.tab ? "active" : ""}" role="tab" data-tab="${key}" type="button">${label}</button>`).join("")}
        </div>
        <div class="cal-tab-host" id="cal-intrinsics-host" ${this.tab === "intrinsics" ? "" : "hidden"}></div>
        <div class="cal-tab-host" id="cal-pose-host" ${this.tab === "pose" ? "" : "hidden"}></div>
        <div class="cal-tab-host" id="cal-pitch-host" ${this.tab === "pitch" ? "" : "hidden"}></div>
      </div>`;
    this.host.querySelectorAll(".cal-tab").forEach((el) => el.addEventListener("click", () => this.showTab(el.dataset.tab)));
  }

  async showTab(tab, force = false) {
    if (!force && tab === this.tab && this.host.querySelector(".cal-tabbar")) return;
    this.tab = tab;
    this.host.querySelectorAll(".cal-tab").forEach((el) => el.classList.toggle("active", el.dataset.tab === tab));
    const hosts = {
      intrinsics: this.host.querySelector("#cal-intrinsics-host"),
      pose: this.host.querySelector("#cal-pose-host"),
      pitch: this.host.querySelector("#cal-pitch-host"),
    };
    for (const [key, el] of Object.entries(hosts)) if (el) el.hidden = key !== tab;

    // One live-preview poll at a time: deactivate the others, activate the visible one.
    if (tab !== "intrinsics") this.intrinsics?.deactivate?.();
    if (tab !== "pose") this.pose?.deactivate?.();
    if (tab !== "pitch") this.pitch?.deactivate?.();

    if (tab === "intrinsics") {
      if (!this.intrinsics) this.intrinsics = new IntrinsicsPanel(hosts.intrinsics);
      await this.intrinsics.activate();
    } else if (tab === "pose") {
      if (!this.pose) this.pose = new PosePanel(hosts.pose);
      await this.pose.activate();
    } else {
      if (!this.pitch) this.pitch = new CalibrationWorkspace(hosts.pitch, this.opts);
      await this.pitch.activate();
    }
  }
}
