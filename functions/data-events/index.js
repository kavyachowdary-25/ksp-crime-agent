const catalyst = require("zcatalyst-sdk-node");

// Data Store event listener target: bump the data version key in Catalyst
// Cache whenever CaseFlat / PersonCaseLink / ResolvedPerson rows change.
// The analytics function polls this key every ~5s and reloads on change.
module.exports = async (event, context) => {
  try {
    const capp = catalyst.initialize(context);
    const segment = capp.cache().segment();
    await segment.put("rainfall_data_version", String(Date.now()), 48);
    context.closeWithSuccess();
  } catch (e) {
    console.error("version bump failed:", e.message);
    context.closeWithFailure();
  }
};
