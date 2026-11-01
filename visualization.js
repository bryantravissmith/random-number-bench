const rows = DATA.rows;
const nums = Array.from({ length: 10 }, (_, i) => String(i + 1));
const names = { baseline: "Baseline", meta: "Sampling meta-prompt" };
const esc = (s) =>
  String(s).replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const pct = (p) => Number((p * 100).toFixed(1)).toString();
let visible = [],
  selected = null;
const rowLabel = (r) =>
  `${r.mode === "requested effort" ? r.effort : r.mode} · T ${r.temperature}`;
function chart(c, prompt) {
  const n = c.accepted_trials;
  if (!n)
    return "<p>No accepted answers. Probabilities and intervals are unavailable.</p>";
  const color = prompt === "baseline" ? "#0072B2" : "#D55E00";
  let svg = `<svg viewBox="0 0 600 280" role="img" aria-label="${names[prompt]} shares and pointwise 95 percent Wilson intervals"><title>${names[prompt]} with ${n} accepted answers</title>`;
  for (const p of [0, 0.1, 0.25, 0.5, 0.75, 1]) {
    const y = 225 - p * 200;
    svg += `<line x1="50" x2="580" y1="${y}" y2="${y}" stroke="${p === 0.1 ? "#555" : "#e5e9e4"}" ${p === 0.1 ? 'stroke-dasharray="5 4"' : ""}/><text x="42" y="${y + 4}" text-anchor="end" fill="#616963" font-size="11">${pct(p)}%</text>`;
  }
  nums.forEach((num, i) => {
    const p = c.counts[num] / n,
      [lo, hi] = c.wilson_intervals[num],
      x = 75 + i * 54,
      y = 225 - p * 200,
      ylo = 225 - lo * 200,
      yhi = 225 - hi * 200;
    svg += `<g><title>Answer ${num}: ${c.counts[num]}/${n}, ${pct(p)}%, 95% interval ${(lo * 100).toFixed(1)}-${(hi * 100).toFixed(1)}%</title><line x1="${x}" x2="${x}" y1="${ylo}" y2="${yhi}" stroke="${color}" stroke-width="2"/><path d="M${x - 4} ${ylo}h8 M${x - 4} ${yhi}h8" stroke="${color}"/>${prompt === "baseline" ? `<circle cx="${x}" cy="${y}" r="4" fill="${color}"/>` : `<rect x="${x - 4}" y="${y - 4}" width="8" height="8" fill="${color}"/>`}<text x="${x}" y="247" text-anchor="middle" font-size="12" fill="#202722">${num}</text></g>`;
  });
  return (
    svg +
    '<text x="315" y="270" text-anchor="middle" font-size="12" fill="#616963">Answer</text></svg>'
  );
}
function renderDetail() {
  const r = visible.find((r) => r.id === selected);
  const panel = document.querySelector("#detail");
  if (!r) {
    panel.innerHTML = "";
    return;
  }
  panel.innerHTML = `<div class="eyebrow" style="color:var(--green)">Selected setting</div><h2 id="detail-title" tabindex="-1">${esc(r.model)}</h2><p class="muted">${esc(rowLabel(r))} · ${esc(r.run)}</p><div class="details">${[
    "baseline",
    "meta",
  ]
    .map((p) => {
      const c = r.conditions[p];
      return `<div class="panel"><h3>${names[p]}</h3>${chart(c, p)}<div class="metrics"><span>Accepted ${c.accepted_trials}/${c.planned_trials}</span><span>Invalid ${c.invalid_response_attempts}</span><span>Operational attempts ${c.operational_exclusions}</span>${c.truncated_response_attempts === undefined ? "" : `<span>Truncated ${c.truncated_response_attempts}</span>`}<span>TV ${c.tv_to_uniform === null ? "n/a" : c.tv_to_uniform.toFixed(3)}</span><span>Effective answers ${r.metrics[p].effective_answers === null ? "n/a" : r.metrics[p].effective_answers.toFixed(2)}</span></div></div>`;
    })
    .join(
      "",
    )}</div><p class="note">Dots and whiskers show shares and pointwise 95% Wilson intervals. Dashed line = 10%. Operational exclusions count physical attempts, which can be retried; they are not mutually exclusive with accepted logical trials.</p><details><summary>Exact counts and interval endpoints</summary><div class="table-scroll"><table class="exact"><thead><tr><th>Answer</th><th>Baseline count / n</th><th>95% interval</th><th>Meta count / n</th><th>95% interval</th></tr></thead><tbody>${nums
    .map(
      (num) =>
        `<tr><th>${num}</th>${["baseline", "meta"]
          .map((p) => {
            const c = r.conditions[p],
              b = c.wilson_intervals[num];
            return `<td>${c.counts[num]} / ${c.accepted_trials}</td><td>${b ? b.map((v) => (100 * v).toFixed(2) + "%").join(" to ") : "n/a"}</td>`;
          })
          .join("")}</tr>`,
    )
    .join(
      "",
    )}</tbody></table></div></details><details><summary>Frozen parameters and provenance</summary><pre>${esc(JSON.stringify({ run: r.run, cell: r.cell, protocol: r.protocol, runner: r.runner, runner_version: r.runner_version, requested_model: r.model, requested_effort: r.effort, profile: r.profile, decoding_note: r.profile ? "Explicit frozen controls" : "Default decoding; temperature not recorded" }, null, 2))}</pre>${r.evidence ? `<a href="${esc(r.evidence)}">Public evidence package</a>` : ""}</details>`;
}
function downloadCSV() {
  const columns = [
    "run",
    "developer",
    "class",
    "model",
    "effort",
    "mode",
    "temperature",
    "runner",
    "upstream",
    "precision",
    "stratum",
    "prompt",
    "answer",
    "count",
    "accepted",
    "planned",
    "invalid",
    "operational_attempts",
    "share",
    "ci_low",
    "ci_high",
    "tv",
    "entropy_bits",
    "effective_answers",
    "exclusion_reason",
  ];
  const out = [columns];
  for (const r of visible)
    for (const prompt of ["baseline", "meta"])
      for (const number of nums) {
        const c = r.conditions[prompt],
          b = c.wilson_intervals[number];
        out.push([
          r.run,
          r.developer,
          r.class,
          r.model,
          r.effort,
          r.mode,
          r.temperature,
          r.runner,
          r.upstream,
          r.precision,
          r.stratum,
          prompt,
          number,
          c.counts[number],
          c.accepted_trials,
          c.planned_trials,
          c.invalid_response_attempts,
          c.operational_exclusions,
          c.accepted_trials ? c.counts[number] / c.accepted_trials : "",
          b?.[0] ?? "",
          b?.[1] ?? "",
          c.tv_to_uniform ?? "",
          r.metrics[prompt].entropy_bits ?? "",
          r.metrics[prompt].effective_answers ?? "",
          r.exclusion_reason ?? "",
        ]);
      }
  const blob = new Blob(
      [
        out
          .map((row) =>
            row
              .map((v) => '"' + String(v).replaceAll('"', '""') + '"')
              .join(","),
          )
          .join("\n"),
      ],
      { type: "text/csv" },
    ),
    url = URL.createObjectURL(blob),
    a = document.createElement("a");
  a.href = url;
  a.download = "random-number-bench-filtered.csv";
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// A profile's on/required mode is not equivalent to a native CLI effort flag.
const displayEffort = (r) =>
  r.profile && ["on", "required"].includes(r.mode) ? "on" : r.effort;
const effortOrder = [
  "off",
  "on",
  "minimal",
  "low",
  "medium",
  "high",
  "xhigh",
  "max",
];
const effortNames = {
  off: "Off / none",
  on: "On / required",
  minimal: "Minimal",
  low: "Low",
  medium: "Medium",
  high: "High",
  xhigh: "Xhigh",
  max: "Max",
};
const dialog = document.querySelector("#detail-dialog");
document.querySelector("#close-detail").onclick = () => dialog.close();
document.querySelector("#download").onclick = downloadCSV;
document.querySelector("#reveal-excluded").onchange = render;
document.querySelector("#gradient").style.background =
  `linear-gradient(to right,${DATA.palette.filter((_, i) => i % 10 === 0).join(",")})`;
document.querySelector("#scope").innerHTML =
  `<ul>${(DATA.scope.notes || []).map((n) => `<li>${esc(n)}</li>`).join("")}</ul>`;

function score(r, prompt) {
  const value = r.metrics[prompt].effective_answers,
    c = r.conditions[prompt];
  const normalized = value === null ? null : (value - 1) / 9;
  const style =
    value === null
      ? ""
      : `background:${DATA.palette[Math.round(normalized * 100)]};color:${normalized < 0.48 ? "#fff" : "#111"}`;
  const label = `${r.developer} ${r.label}, ${r.mode}, requested effort ${r.effort}, temperature ${r.temperature}; ${names[prompt]}: ${value === null ? "no accepted answers" : value.toFixed(2) + " effective answers"}, ${c.accepted_trials}/${c.planned_trials} accepted. Open distribution and confidence intervals.`;
  return `<button class="score ${value === null ? "no-data" : ""}" style="${style}" data-row="${esc(r.id)}" data-prompt="${prompt}" title="${esc(label)}" aria-label="${esc(label)}">${value === null ? "n/a" : value.toFixed(1)}${c.accepted_trials < c.planned_trials ? "<sup>*</sup>" : ""}</button>`;
}
function render() {
  visible = rows.filter(
    (r) =>
      !r.exclusion_reason || document.querySelector("#reveal-excluded").checked,
  );
  const sampleSizes = [
    ...new Set(
      visible.flatMap((r) =>
        Object.values(r.conditions).map((c) => c.planned_trials),
      ),
    ),
  ];
  document.querySelector("#sample-size").textContent =
    sampleSizes.length === 1
      ? `${sampleSizes[0]} trials per prompt`
      : "Planned sample sizes vary; click for denominators";
  const efforts = effortOrder.filter((e) =>
    visible.some((r) => displayEffort(r) === e),
  );
  const groups = new Map();
  // Sorting is by the curated display order, never by a measured score.
  for (const r of visible) {
    const key = JSON.stringify([
      Boolean(r.exclusion_reason),
      r.developer,
      r.group,
    ]);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(r);
  }
  let html = `<thead><tr><th class="family" scope="col">Family</th><th class="version" scope="col">Model / version</th><th class="temp" scope="col">Temp.</th>${efforts.map((e) => `<th class="effort" scope="col">${effortNames[e]}<span class="pair-label"><span>B</span><span>M</span></span></th>`).join("")}</tr></thead>`;
  for (const group of [...groups.values()].sort(
    (a, b) =>
      Number(Boolean(a[0].exclusion_reason)) -
        Number(Boolean(b[0].exclusion_reason)) ||
      a[0].developer.localeCompare(b[0].developer) ||
      Math.min(...a.map((r) => r.version_order)) -
        Math.min(...b.map((r) => r.version_order)),
  )) {
    const r = group[0];
    const upstream =
      r.upstream === "CLI managed"
        ? "subscription CLI · default decoding"
        : `${r.upstream} · ${r.precision}`;
    const context = `${r.runner} · ${upstream} · ${r.run}`;
    html += `<tbody><tr class="section ${r.exclusion_reason ? "excluded" : ""}"><th colspan="${efforts.length + 3}" scope="colgroup">${esc(r.developer)}${r.exclusion_reason ? " · excluded archive" : ""}<span class="context">${esc(context)}</span></th></tr>`;
    const modelRows = new Map();
    for (const setting of group) {
      const key = JSON.stringify([setting.model, setting.temperature]);
      if (!modelRows.has(key)) modelRows.set(key, []);
      modelRows.get(key).push(setting);
    }
    let previousModel = null;
    const ordered = [...modelRows.values()].sort(
      (a, b) =>
        a[0].class.localeCompare(b[0].class) ||
        a[0].version_order - b[0].version_order ||
        a[0].model.localeCompare(b[0].model) ||
        Number(a[0].temperature) - Number(b[0].temperature),
    );
    for (const settings of ordered) {
      const first = settings[0],
        repeated = previousModel === first.model;
      const byEffort = new Map();
      for (const setting of settings) {
        if (byEffort.has(displayEffort(setting)))
          throw new Error(
            "Ambiguous model/effort row: refusing to pool settings",
          );
        byEffort.set(displayEffort(setting), setting);
      }
      html += `<tr class="${repeated ? "" : "model-start"}"><td class="family">${repeated ? "" : esc(first.class)}</td><th scope="row" class="version ${repeated ? "model-repeat" : ""}" title="${esc(first.model)}">${esc(first.label)}</th><td class="temp" title="${esc(first.temperature)}">${first.temperature === "default / unknown" ? "default" : esc(first.temperature)}</td>${efforts
        .map((e) => {
          const cell = byEffort.get(e);
          return cell
            ? `<td class="slot"><div class="pair">${score(cell, "baseline")}${score(cell, "meta")}</div></td>`
            : '<td class="missing" aria-label="No completed cell in this snapshot">-</td>';
        })
        .join("")}</tr>`;
      previousModel = first.model;
    }
    html += "</tbody>";
  }
  document.querySelector("#matrix").innerHTML = html;
  document.querySelector("#inventory").textContent =
    `${new Set(visible.map((r) => r.model)).size} models · ${visible.length} settings · ${visible.reduce((n, r) => n + Object.values(r.conditions).reduce((s, c) => s + c.accepted_trials, 0), 0).toLocaleString()} accepted answers · completed grids only · ${DATA.scope.snapshot_date || "snapshot"}. ${rows.some((r) => r.exclusion_reason) ? "Excluded archive hidden by default." : ""}`;
  document.querySelectorAll("[data-row]").forEach(
    (button) =>
      (button.onclick = () => {
        selected = button.dataset.row;
        renderDetail();
        dialog.showModal();
      }),
  );
}
render();
