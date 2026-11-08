(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.S23PriceCore = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const WITHIN_BUDGET_MAX = 340000;
  const NEAR_BUDGET_MAX = 370000;
  const MODELS = ['s23', 's23_fe', 's23_plus', 's24_fe'];
  // Manufacturer support pledges (S1): full months of security updates left
  // per tracked model, per Samsung's published support windows.
  const MODEL_UPDATE_SUPPORT = {
    s23: { security_until: '2028-02', support_pledge: '4 OS + 5 años de seguridad (feb 2023)' },
    s23_plus: { security_until: '2028-02', support_pledge: '4 OS + 5 años de seguridad (feb 2023)' },
    s23_fe: { security_until: '2028-02', support_pledge: '4 OS + 5 años de seguridad (feb 2023)' },
    s24_fe: { security_until: '2031-09', support_pledge: '7 años de OS y seguridad (sep 2024)' },
  };
  // Per-model spec sheet for the side-by-side comparison section (S5). Battery
  // and chips follow the same regional reality the processor section explains:
  // security_until mirrors MODEL_UPDATE_SUPPORT so the windows never drift apart.
  const MODEL_SPECS = {
    s23: {
      chip_label: 'Snapdragon 8 Gen 2 for Galaxy',
      chip_variants: [{ chip: 'Snapdragon 8 Gen 2 for Galaxy', region: 'todas las regiones' }],
      battery_mah: 3900,
      security_until: '2028-02',
    },
    s23_plus: {
      chip_label: 'Snapdragon 8 Gen 2 for Galaxy',
      chip_variants: [{ chip: 'Snapdragon 8 Gen 2 for Galaxy', region: 'todas las regiones' }],
      battery_mah: 4700,
      security_until: '2028-02',
    },
    s23_fe: {
      chip_label: 'Exynos 2200 (Chile) · Snapdragon 8 Gen 1 (EE. UU.)',
      chip_variants: [
        { chip: 'Exynos 2200', region: 'Chile / global' },
        { chip: 'Snapdragon 8 Gen 1', region: 'EE. UU. / importado' },
      ],
      battery_mah: 4500,
      security_until: '2028-02',
    },
    s24_fe: {
      chip_label: 'Exynos 2400e',
      chip_variants: [{ chip: 'Exynos 2400e', region: 'todas las regiones' }],
      battery_mah: 4700,
      security_until: '2031-09',
    },
  };
  const HISTOGRAM_BINS = [
    { min: 0, max: 250000, label: 'Hasta $250k' },
    { min: 250001, max: 280000, label: '$250k–$280k' },
    { min: 280001, max: 310000, label: '$280k–$310k' },
    { min: 310001, max: 340000, label: '$310k–$340k' },
    { min: 340001, max: 370000, label: '$340k–$370k' },
  ];

  // Reads buyer-configured caps from the history file (S4). Falls back to the
  // project defaults when absent or invalid.
  function budgetFor(history) {
    const raw = history && history.budget;
    const within = raw && raw.within_max;
    const near = raw && raw.near_max;
    if (Number.isSafeInteger(within) && within > 0 &&
        Number.isSafeInteger(near) && near > within) {
      return { within_max: within, near_max: near };
    }
    return { within_max: WITHIN_BUDGET_MAX, near_max: NEAR_BUDGET_MAX };
  }

  function priceBand(price, withinMax, nearMax) {
    if (!Number.isSafeInteger(price) || price <= 0) return 'excluded';
    const within = withinMax ?? WITHIN_BUDGET_MAX;
    const near = nearMax ?? NEAR_BUDGET_MAX;
    if (price <= within) return 'within_budget';
    if (price <= near) return 'near_budget';
    return 'excluded';
  }

  function validSnapshots(history) {
    const snapshots = history && history.snapshots;
    if (!snapshots || typeof snapshots !== 'object' || Array.isArray(snapshots)) return [];
    return Object.entries(snapshots)
      .filter(([, snapshot]) => snapshot && Array.isArray(snapshot.offers))
      .sort(([weekA], [weekB]) => weekA.localeCompare(weekB));
  }

  function latestSnapshot(history) {
    const snapshots = validSnapshots(history);
    if (!snapshots.length) return null;
    const [week, snapshot] = snapshots[snapshots.length - 1];
    return { week, ...snapshot };
  }

  function isTrackableOffer(offer, budget) {
    const caps = budget || { within_max: WITHIN_BUDGET_MAX, near_max: NEAR_BUDGET_MAX };
    return offer && MODELS.includes(offer.model) &&
      priceBand(offer.price, caps.within_max, caps.near_max) !== 'excluded' &&
      offer.available !== false;
  }

  function currentOffers(history) {
    const snapshot = latestSnapshot(history);
    if (!snapshot) return [];
    const budget = budgetFor(history);
    return snapshot.offers.filter((offer) => isTrackableOffer(offer, budget))
      .map((offer) => ({
        ...offer,
        band: priceBand(offer.price, budget.within_max, budget.near_max),
      }))
      .sort((a, b) => MODELS.indexOf(a.model) - MODELS.indexOf(b.model) ||
        a.price - b.price || String(a.title || '').localeCompare(String(b.title || '')));
  }

  function histogramBins(budget) {
    // With the project default caps the classic curated bins are used; user
    // caps get evenly chunked bins so the chart stays truthful.
    if (budget && (budget.within_max !== WITHIN_BUDGET_MAX ||
                   budget.near_max !== NEAR_BUDGET_MAX)) {
      const near = budget.near_max;
      const step = Math.round(near / 5 / 10000) * 10000 || 1;
      const edges = [0];
      for (let value = step; value < near; value += step) edges.push(value);
      edges.push(near);
      return edges.slice(0, -1).map((edge, index) => ({
        min: edge + (index === 0 ? 0 : 1),
        max: edges[index + 1],
        label: index === 0
          ? `Hasta $${Math.round(edges[index + 1] / 1000)}k`
          : `$${Math.round((edge + (index === 0 ? 0 : 1)) / 1000)}k–$${Math.round(edges[index + 1] / 1000)}k`,
      }));
    }
    return HISTOGRAM_BINS;
  }

  function buildPriceHistogram(offers, budget) {
    return histogramBins(budget).map((bin) => ({
      ...bin,
      count: (Array.isArray(offers) ? offers : []).filter((offer) =>
        isTrackableOffer(offer, budget) && offer.price >= bin.min &&
        offer.price <= bin.max).length,
    }));
  }

  function buildPriceTrend(history) {
    const snapshots = validSnapshots(history);
    const budget = budgetFor(history);
    const series = Object.fromEntries(MODELS.map((model) => [model, []]));
    const weeks = [];
    for (const [week, snapshot] of snapshots) {
      weeks.push(week);
      for (const model of MODELS) {
        const prices = snapshot.offers.filter((offer) =>
          isTrackableOffer(offer, budget) && offer.model === model)
          .map((offer) => offer.price);
        series[model].push(prices.length ? Math.min(...prices) : null);
      }
    }
    return { weeks, series };
  }

  function processorFor(offer) {
    if (offer && typeof offer.processor === 'string' &&
        offer.processor !== 'unknown' && offer.processor.trim()) return offer.processor;
    if (!offer) return 'Por confirmar: revisa el código de modelo';
    if (offer.model === 's23' || offer.model === 's23_plus') {
      return 'Snapdragon 8 Gen 2 for Galaxy';
    }
    if (offer.model === 's24_fe') return 'Exynos 2400e';
    const code = String(offer.model_code || '').toUpperCase();
    if (code.startsWith('SM-S711B')) return 'Exynos 2200';
    if (code.startsWith('SM-S711U')) return 'Snapdragon 8 Gen 1';
    return 'Por confirmar: revisa el código de modelo';
  }

  function cheapestOffer(offers, model, budget) {
    return (Array.isArray(offers) ? offers : [])
      .filter((offer) => isTrackableOffer(offer, budget) && offer.model === model)
      .reduce((best, offer) => !best || offer.price < best.price ? offer : best, null);
  }

  function compareModelPrices(offers) {
    const cheapestFor = Object.fromEntries(
      MODELS.map((model) => [model, cheapestOffer(offers, model)]));
    return {
      ...cheapestFor,
      s23Premium: cheapestFor.s23 && cheapestFor.s23_fe
        ? cheapestFor.s23.price - cheapestFor.s23_fe.price
        : null,
    };
  }

  function formatCLP(value) {
    if (!Number.isSafeInteger(value)) return '—';
    return new Intl.NumberFormat('es-CL', {
      style: 'currency', currency: 'CLP', maximumFractionDigits: 0,
    }).format(value);
  }

  function updateMonthsLeft(model, now) {
    // Whole calendar months from `now` (default: the current month) until the
    // manufacturer security window ends; always >= 0, null for unknown models.
    const meta = model && MODEL_UPDATE_SUPPORT[model];
    if (!meta) return null;
    const [year, month] = meta.security_until.split('-').map(Number);
    const reference = now instanceof Date && !Number.isNaN(now.getTime())
      ? now : new Date();
    const elapsed =(reference.getFullYear() * 12 + reference.getMonth());
    const remainingMax = year * 12 + (month - 1);
    return Math.max(0, remainingMax - elapsed);
  }

  return {
    WITHIN_BUDGET_MAX,
    NEAR_BUDGET_MAX,
    priceBand,
    latestSnapshot,
    currentOffers,
    buildPriceHistogram,
    buildPriceTrend,
    processorFor,
    compareModelPrices,
    formatCLP,
    budgetFor,
    MODEL_UPDATE_SUPPORT,
    MODEL_SPECS,
    updateMonthsLeft,
  };
});
