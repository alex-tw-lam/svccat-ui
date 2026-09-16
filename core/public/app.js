// svccat-ui client sprinkle: loaded sync in <head> BEFORE htmx/Alpine, so the console filter, delegated data-confirm handler and alpine:init register first. The server stays the authority — the server re-validates every client check. Transient blips on the 3s polls self-heal; htmx reports each via console.error — filter EXACTLY those strings; real errors pass through.
((orig) => {
  console.error = (...args) => {
    const t = args.map(String).join(" ");
    if (
      t === "htmx:sendError" ||
      t === "htmx:sendAbort" ||
      t === "htmx:afterRequest"
    )
      return;
    orig(...args);
  };
})(console.error);
// Delete confirmations: the message travels in a data-confirm ATTRIBUTE (HTML-escaped attribute context). Display names accept ANY characters, so they must never be interpolated into a JS string context — entity encoding does not protect there (the HTML parser decodes attribute values before the JS engine parses them). One delegated capture-phase submit handler covers every delete form.
document.addEventListener(
  "submit",
  (e) => {
    const form = e.target instanceof HTMLFormElement ? e.target : null;
    if (form && form.dataset.confirm && !window.confirm(form.dataset.confirm))
      e.preventDefault();
  },
  true,
);
// Namespace switcher (topbar scope dropdown): keeps the current path and every other query parameter, swaps only ?tenant=.
document.addEventListener("change", ({ target }) => {
  const sel =
    target instanceof HTMLSelectElement
      ? target.closest("[data-tenant-switch]")
      : null;
  if (sel) {
    const url = new URL(location);
    url.searchParams.set("tenant", sel.value);
    location = url;
  }
});
// Client-side validation mirror of core/input_schema.py: same layers (computed > plan > user, platform-pinned), same rejection reasons, same check order; the drawer's raw-JSON textarea is checked on every keystroke and fails closed — the server re-validates the identical contract.
function smValidateParams(text, contract) {
  if (!text.trim()) return null; // absent parameters is valid
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch {
    return `${contract.op === "bind" ? "Bind parameters" : "Parameters"} are not valid JSON — fix before submitting`;
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed))
    return "parameters must be a JSON object";
  const editable = contract.editable || [];
  for (const key of Object.keys(parsed)) {
    const value = parsed[key];
    if ((contract.computed || []).indexOf(key) !== -1) {
      if (contract.op === "bind")
        return `"${key}" is a computed bind input - projected from the instance`;
      return `"${key}" is a computed input - controlled by the broker`;
    }
    if ((contract.plan || []).indexOf(key) !== -1)
      return `"${key}" is a plan input - locked by the selected plan (pick another plan instead)`;
    if (
      contract.pinned &&
      Object.prototype.hasOwnProperty.call(contract.pinned, key)
    )
      return `"${key}" is ${contract.pinned[key]}`;
    const def = (contract.user || {})[key];
    if (!def) {
      const known = editable.join(", ") || "none";
      if (contract.op === "bind")
        return `unknown bind parameter "${key}" - bind user inputs for this offering: ${known}`;
      return `unknown parameter "${key}" - user inputs for this offering: ${known}`;
    }
    if (def.type === "string" && typeof value !== "string")
      return `"${key}" must be a string`;
    if (def.type === "number" && typeof value !== "number")
      return `"${key}" must be a number`;
    if (def.type === "boolean" && typeof value !== "boolean")
      return `"${key}" must be a boolean`;
    if (
      def.pattern &&
      typeof value === "string" &&
      !new RegExp(def.pattern).test(value)
    )
      return `"${key}" does not match required pattern ${def.pattern}`;
    if (def.enum && def.enum.indexOf(value) === -1)
      return `"${key}" must be one of: ${def.enum.join(", ")}`;
  }
  return null;
}
document.addEventListener("alpine:init", () => {
  // Free-input drawer on the provision/edit/bind forms: raw-JSON textarea (the POST input, x-model), drawer toggle, schema modal, the busy flag, and the keystroke gate above.
  Alpine.data("smDrawer", (opts = {}) => ({
    open: !!opts.open,
    busy: false,
    schemaOpen: false,
    submittable: opts.submittable !== false,
    text: "",
    rev: 0,
    init() {
      const ta = this.$root.querySelector('textarea[name="parameters"]');
      if (ta) this.text = ta.value || "";
      this.$root.addEventListener("input", () => this.rev++);
    }, // seed text from the server prefill (edit / failed-post re-render); rev bumps re-render the CR preview on any field edit
    get contract() {
      try {
        return JSON.parse(this.$refs.contract.textContent);
      } catch {
        return null;
      }
    }, // re-read every access: htmx swaps refresh it
    get error() {
      if (!this.text || !this.text.trim() || !this.contract) return null;
      return smValidateParams(this.text, this.contract);
    },
    // Live YAML preview of the CR this form will submit (Headlamp's
    // form-beside-editor pattern). Shapes mirror core/kube.py exactly:
    // instance parameters always carry the platform-pinned
    // namespace/instance_name (pinned last), namespaced offerings reference
    // UUIDs the server resolves, bindings carry instanceRef + secretName.
    // Values unknowable client-side render as «placeholders».
    crYaml() {
      this.rev; // DOM .value reads are not Alpine-reactive; touching rev re-renders on every field edit
      const f = this.$root;
      const name =
        f.querySelector('[name="name"]')?.value.trim() ||
        f.dataset.crName ||
        "«name»";
      const ns =
        f.closest("[data-tenant]")?.dataset.tenant || "«namespace»";
      let params = null;
      try {
        const parsed = JSON.parse(this.text || "{}");
        if (
          parsed &&
          typeof parsed === "object" &&
          !Array.isArray(parsed) &&
          Object.keys(parsed).length
        )
          params = parsed;
      } catch {
        params = "«invalid JSON»";
      }
      const line = (k, v) => (v ? `  ${k}: ${JSON.stringify(v)}\n` : "");
      // parameters render as a nested YAML map (not one long JSON line):
      // user inputs first, platform-pinned keys last — the merge order
      // core/kube.py applies server-side. A string here means invalid JSON.
      const paramsBlock = (obj) =>
        typeof obj === "string"
          ? `  parameters: ${JSON.stringify(obj)}\n`
          : "  parameters:\n" +
            Object.entries(obj)
              .map(
                ([k, v]) =>
                  `    ${/^[$_A-Za-z][$_A-Za-z0-9]*$/.test(k) ? k : JSON.stringify(k)}: ${JSON.stringify(v)}\n`,
              )
              .join("");
      const inst = f.querySelector('[name="instanceId"]');
      if (inst)
        return (
          "apiVersion: servicecatalog.k8s.io/v1beta1\n" +
          "kind: ServiceBinding\n" +
          `metadata:\n  name: ${name}\n  namespace: ${ns}\n` +
          "spec:\n" +
          `  instanceRef:\n    name: ${inst.value}\n` +
          (params ? paramsBlock(params) : "") +
          line("secretName", `binding-${name}`)
        );
      const offering =
        f.querySelector('[name="offeringId"]')?.value ||
        f.dataset.offering ||
        "";
      const plan = f.querySelector('[name="planId"]')?.value || "";
      const spec = f.dataset.namespaced
        ? "  serviceClassName: «uuid»\n  servicePlanName: «uuid»\n"
        : line("clusterServiceClassExternalName", offering) +
          line("clusterServicePlanExternalName", plan);
      return (
        "apiVersion: servicecatalog.k8s.io/v1beta1\n" +
        "kind: ServiceInstance\n" +
        `metadata:\n  name: ${name}\n  namespace: ${ns}\n` +
        "spec:\n" +
        spec +
        paramsBlock(
          params === "«invalid JSON»"
            ? params
            : { ...(params || {}), namespace: ns, instance_name: name },
        )
      );
    },
  }));
  Alpine.data("smCopy", () => ({
    copied: false,
    copy() {
      const text = this.$refs.creds ? this.$refs.creds.innerText : "";
      const done = () => {
        this.copied = true;
        setTimeout(() => {
          this.copied = false;
        }, 2000);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done).catch(done);
      } else done();
    },
  })); // Copy-to-clipboard for revealed credentials (already on screen; copying must not re-fetch)
});
