// Choose a currency on a document form the way the page does, answer the
// exchange-rate lookup when the probe says so, and print the rate field.
// Prints one JSON object for the pytest side.
const fs = require('fs'), vm = require('vm');

const pending = [];
const ctx = {
  console,
  window: {},
  document: { addEventListener: () => {} },
  API: {
    get: (path) => new Promise((resolve) => { pending.push({ path, resolve }); }),
  },
};
vm.createContext(ctx);
vm.runInContext(
  fs.readFileSync('app/static/js/utils.js', 'utf8') + '\nthis.prefillFxRate = prefillFxRate;',
  ctx,
);

function form() {
  const listeners = {};
  const rate = {
    value: '1', dataset: {},
    addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
  };
  const f = { querySelector: (sel) => (sel === '[name=exchange_rate]' ? rate : null) };
  const select = { value: 'USD', closest: () => f };
  const type = (v) => { rate.value = v; (listeners.input || []).forEach(fn => fn()); };
  return { rate, select, type };
}

const settle = () => new Promise((r) => setImmediate(r));

(async () => {
  const out = {};

  // nobody types: the looked-up rate fills the field
  let F = form();
  F.select.value = 'EUR';
  let done = ctx.prefillFxRate(F.select);
  pending.shift().resolve({ rate: 1.1397667 });
  await done;
  out.untouched = F.rate.value;

  // a rate typed while the lookup answers stays
  F = form();
  F.select.value = 'EUR';
  done = ctx.prefillFxRate(F.select);
  F.type('1.10');
  pending.shift().resolve({ rate: 1.1397667 });
  await done;
  out.typed = F.rate.value;

  // an answer for a currency since changed is dropped
  F = form();
  F.select.value = 'EUR';
  const first = ctx.prefillFxRate(F.select);
  const eur = pending.shift();
  F.select.value = 'GBP';
  const second = ctx.prefillFxRate(F.select);
  const gbp = pending.shift();
  gbp.resolve({ rate: 1.27 });
  await second;
  eur.resolve({ rate: 1.1397667 });
  await first;
  out.changed = { value: F.rate.value, paths: [eur.path, gbp.path] };

  // typed for one currency, then another chosen: the new rate fills
  F = form();
  F.select.value = 'EUR';
  done = ctx.prefillFxRate(F.select);
  F.type('1.10');
  pending.shift().resolve({ rate: 1.1397667 });
  await done;
  F.select.value = 'CAD';
  done = ctx.prefillFxRate(F.select);
  pending.shift().resolve({ rate: 0.73 });
  await done;
  out.rechosen = F.rate.value;

  await settle();
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
