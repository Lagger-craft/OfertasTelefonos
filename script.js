const tabs = [...document.querySelectorAll('[role="tab"]')];
const panels = [...document.querySelectorAll('[role="tabpanel"]')];
const core = window.S23PriceCore;
const SVG_NS = 'http://www.w3.org/2000/svg';

function activateTab(nextTab, moveFocus = false) {
  const model = nextTab.dataset.model;

  tabs.forEach((tab) => {
    const selected = tab === nextTab;
    tab.classList.toggle('is-active', selected);
    tab.setAttribute('aria-selected', String(selected));
    tab.tabIndex = selected ? 0 : -1;
  });

  panels.forEach((panel) => {
    const selected = panel.dataset.panel === model;
    panel.hidden = !selected;
    panel.classList.toggle('is-active', selected);
  });

  if (moveFocus) nextTab.focus();
}

tabs.forEach((tab, index) => {
  tab.addEventListener('click', () => activateTab(tab));
  tab.addEventListener('keydown', (event) => {
    let nextIndex;
    if (event.key === 'ArrowRight') nextIndex = (index + 1) % tabs.length;
    if (event.key === 'ArrowLeft') nextIndex = (index - 1 + tabs.length) % tabs.length;
    if (event.key === 'Home') nextIndex = 0;
    if (event.key === 'End') nextIndex = tabs.length - 1;
    if (nextIndex !== undefined) {
      event.preventDefault();
      activateTab(tabs[nextIndex], true);
    }
  });
});

function byId(id) {
  return document.getElementById(id);
}

function setText(id, value) {
  const node = byId(id);
  if (node) node.textContent = value;
}

function sourceLabel(source) {
  const host = new URL(source).hostname.toLowerCase();
  if (host.includes('reuse')) return 'Reuse Chile';
  if (host.includes('bodegaoportunidades')) return 'Bodega Oportunidades';
  if (host.includes('dlphone')) return 'DL Phone';
  return host;
}

function safeOfferUrl(offer) {
  if (!offer || typeof offer.url !== 'string') return null;
  try {
    const target = new URL(offer.url);
    const sourceHost = new URL(offer.source).hostname.toLowerCase();
    const allowed = sourceHost === 'reusecl.myshopify.com'
      ? ['reusecl.myshopify.com', 'reuse.cl', 'www.reuse.cl']
      : sourceHost === 'bogedaoportunidades.myshopify.com'
        ? ['bogedaoportunidades.myshopify.com', 'bodegaoportunidades.cl', 'www.bodegaoportunidades.cl']
        : sourceHost === 'dlphone.cl'
          ? ['dlphone.cl', 'www.dlphone.cl', 'd8f7f8-76.myshopify.com']
          : [sourceHost];
    if (target.protocol !== 'https:' || !allowed.includes(target.hostname.toLowerCase()) ||
        target.username || target.password || target.port) return null;
    return target.href;
  } catch {
    return null;
  }
}

function offerModelLabel(model) {
  return model === 's23_fe' ? 'Galaxy S23 FE' : 'Galaxy S23';
}

function makeCell(label) {
  const cell = document.createElement('td');
  cell.dataset.label = label;
  return cell;
}

function createOfferRow(offer, index) {
  const row = document.createElement('tr');
  if (index === 0) row.classList.add('best-deal');

  const storeCell = makeCell('Tienda');
  const store = document.createElement('span');
  store.className = 'store-name';
  store.textContent = sourceLabel(offer.source);
  const listing = document.createElement('span');
  listing.className = 'store-sub';
  listing.textContent = offer.title || offerModelLabel(offer.model);
  const processor = document.createElement('span');
  processor.className = 'store-sub processor-label';
  processor.textContent = core.processorFor(offer);
  storeCell.append(store, listing, processor);

  const priceCell = makeCell('Precio');
  const price = document.createElement('span');
  price.className = 'price';
  price.textContent = core.formatCLP(offer.price);
  const tag = document.createElement('span');
  tag.className = offer.band === 'near_budget' ? 'price-tag price-tag-warn' : 'price-tag';
  tag.textContent = offer.band === 'near_budget'
    ? `CERCA DEL TOPE · +${core.formatCLP(offer.price - core.WITHIN_BUDGET_MAX)}`
    : index === 0 ? 'MEJOR PRECIO EN ESTA LISTA' : 'DENTRO DEL TOPE';
  priceCell.append(price, tag);

  const detailCell = makeCell('Detalle');
  const model = document.createElement('span');
  model.className = 'offer-model';
  model.textContent = `${offerModelLabel(offer.model)} · ${offer.model_code || 'código por confirmar'}`;
  const availability = document.createElement('span');
  availability.className = 'store-sub';
  availability.textContent = offer.available === true
    ? 'Stock informado en la última consulta'
    : 'Disponibilidad no informada; confirma con la tienda';
  const battery = document.createElement('span');
  battery.className = 'store-sub';
  battery.textContent = 'Salud de batería no informada en la ficha';
  detailCell.append(model, availability, battery);

  const linkCell = makeCell('Enlace');
  const href = safeOfferUrl(offer);
  if (href) {
    const link = document.createElement('a');
    link.className = 'link-button';
    link.href = href;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.append(document.createTextNode('VER FICHA '));
    const arrow = document.createElement('span');
    arrow.setAttribute('aria-hidden', 'true');
    arrow.textContent = '↗';
    link.append(arrow);
    linkCell.append(link);
  } else {
    linkCell.textContent = 'Enlace no disponible';
  }

  row.append(storeCell, priceCell, detailCell, linkCell);
  return row;
}

function renderOfferTable(model, offers) {
  const tbody = byId(model === 's23_fe' ? 'offers-fe' : 'offers-s23');
  if (!tbody) return;
  tbody.replaceChildren();
  const matching = offers.filter((offer) => offer.model === model);
  if (!matching.length) {
    const row = document.createElement('tr');
    row.className = 'empty-row';
    const cell = document.createElement('td');
    cell.colSpan = 4;
    cell.textContent = 'No hay ofertas reacondicionadas en el rango de hasta $370.000 en la última captura.';
    row.append(cell);
    tbody.append(row);
    return;
  }
  matching.forEach((offer, index) => tbody.append(createOfferRow(offer, index)));
}

function formatSnapshotDate(value) {
  if (typeof value !== 'string') return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat('es-CL', {
    dateStyle: 'medium', timeStyle: 'short', timeZone: 'America/Santiago',
  }).format(date);
}

function renderPriceSummary(history, offers) {
  const comparison = core.compareModelPrices(offers);
  setText('best-fe-price', comparison.s23_fe ? core.formatCLP(comparison.s23_fe.price) : '—');
  setText('best-s23-price', comparison.s23 ? core.formatCLP(comparison.s23.price) : '—');
  setText('count-fe', String(offers.filter((offer) => offer.model === 's23_fe').length).padStart(2, '0'));
  setText('count-s23', String(offers.filter((offer) => offer.model === 's23').length).padStart(2, '0'));

  const snapshot = core.latestSnapshot(history);
  if (!snapshot) {
    setText('price-update-status', 'Sin capturas semanales todavía.');
    return;
  }
  const collectedAt = formatSnapshotDate(history.last_collected_at || snapshot.collected_at);
  const failedSources = Object.values(snapshot.sources || {}).filter((status) =>
    typeof status === 'string' && status.startsWith('error:')).length;
  const freshness = collectedAt ? `Última captura: ${collectedAt}` : `Última semana: ${snapshot.week}`;
  setText('price-update-status', failedSources
    ? `${freshness} · ${failedSources} fuente(s) no respondieron`
    : freshness);
}

function renderPriceAdvice(offers) {
  const { s23, s23_fe: fe, s23Premium } = core.compareModelPrices(offers);
  if (!s23 && !fe) {
    setText('price-advice', 'Cuando haya precios actuales, compararemos el menor de cada modelo. En general, el S23 favorece potencia y eficiencia; el S23 FE puede convenir si cuesta bastante menos y su batería está en buen estado.');
    return;
  }
  if (s23 && fe) {
    const difference = Math.abs(s23Premium);
    const relation = s23Premium > 0 ? 'más caro' : s23Premium < 0 ? 'más barato' : 'al mismo precio';
    setText('price-advice', `En la última captura, el S23 está ${relation} que el S23 FE por ${core.formatCLP(difference)}. El S23 tiene una plataforma más nueva y potente; el FE puede ser mejor valor si la diferencia de precio compensa y el vendedor confirma una batería saludable.`);
    return;
  }
  const only = s23 || fe;
  setText('price-advice', `La última captura sólo tiene ofertas del ${offerModelLabel(only.model)} (${core.formatCLP(only.price)} como menor precio). Para elegir valor frente al otro modelo hace falta que aparezca también en el historial.`);
}

function svgElement(name, attributes = {}, text = null) {
  const element = document.createElementNS(SVG_NS, name);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, String(value));
  if (text !== null) element.textContent = text;
  return element;
}

function resetSvg(svg, titleId, descriptionId, description) {
  const title = document.createElementNS(SVG_NS, 'title');
  title.id = titleId;
  title.textContent = svg.querySelector(`#${titleId}`)?.textContent || '';
  const desc = document.createElementNS(SVG_NS, 'desc');
  desc.id = descriptionId;
  desc.textContent = description;
  svg.replaceChildren(title, desc);
}

function drawTrend(history) {
  const svg = byId('trend-chart');
  const empty = byId('trend-empty');
  if (!svg || !empty) return;
  const trend = core.buildPriceTrend(history);
  const allValues = [...trend.series.s23, ...trend.series.s23_fe].filter(Number.isFinite);
  if (!trend.weeks.length || !allValues.length) {
    svg.hidden = true;
    empty.hidden = false;
    return;
  }
  svg.hidden = false;
  empty.hidden = true;
  resetSvg(svg, 'trend-svg-title', 'trend-svg-desc',
    `Menor precio semanal observado para Galaxy S23 y S23 FE durante ${trend.weeks.length} semanas. Valores en pesos chilenos.`);

  const width = 720;
  const height = 280;
  const plot = { left: 68, right: 20, top: 18, bottom: 48 };
  const plotWidth = width - plot.left - plot.right;
  const plotHeight = height - plot.top - plot.bottom;
  let min = Math.min(...allValues);
  let max = Math.max(...allValues);
  if (min === max) { min = Math.max(0, min - 15000); max += 15000; }
  const xFor = (index) => trend.weeks.length === 1
    ? plot.left + plotWidth / 2
    : plot.left + index * plotWidth / (trend.weeks.length - 1);
  const yFor = (value) => plot.top + (max - value) * plotHeight / (max - min);

  for (let tick = 0; tick <= 4; tick += 1) {
    const value = min + (max - min) * tick / 4;
    const y = yFor(value);
    svg.append(svgElement('line', { x1: plot.left, y1: y, x2: width - plot.right, y2: y, class: 'chart-gridline' }));
    svg.append(svgElement('text', { x: plot.left - 9, y: y + 4, 'text-anchor': 'end', class: 'chart-axis-label' },
      `$${Math.round(value / 1000)}k`));
  }

  const seriesInfo = [
    { key: 's23', className: 'chart-line-s23', pointClass: 'chart-point-s23' },
    { key: 's23_fe', className: 'chart-line-fe', pointClass: 'chart-point-fe' },
  ];
  for (const info of seriesInfo) {
    let segment = [];
    const flush = () => {
      if (segment.length > 1) {
        svg.append(svgElement('polyline', {
          points: segment.map(({ x, y }) => `${x},${y}`).join(' '),
          class: info.className,
        }));
      }
      for (const point of segment) {
        const circle = svgElement('circle', { cx: point.x, cy: point.y, r: 4, class: info.pointClass });
        circle.append(svgElement('title', {}, `${point.week}: ${core.formatCLP(point.value)}`));
        svg.append(circle);
      }
      segment = [];
    };
    trend.series[info.key].forEach((value, index) => {
      if (value === null || !Number.isFinite(value)) { flush(); return; }
      segment.push({ x: xFor(index), y: yFor(value), value, week: trend.weeks[index] });
    });
    flush();
  }

  const labelStep = Math.max(1, Math.ceil(trend.weeks.length / 6));
  trend.weeks.forEach((week, index) => {
    if (index % labelStep !== 0 && index !== trend.weeks.length - 1) return;
    svg.append(svgElement('text', {
      x: xFor(index), y: height - 15, 'text-anchor': 'middle', class: 'chart-axis-label',
    }, week));
  });
}

function drawHistogram(offers) {
  const svg = byId('histogram-chart');
  const empty = byId('histogram-empty');
  if (!svg || !empty) return;
  const bins = core.buildPriceHistogram(offers);
  const total = bins.reduce((sum, bin) => sum + bin.count, 0);
  if (!total) {
    svg.hidden = true;
    empty.hidden = false;
    return;
  }
  svg.hidden = false;
  empty.hidden = true;
  resetSvg(svg, 'histogram-svg-title', 'histogram-svg-desc',
    `Distribución de ${total} ofertas vigentes por rango de precio en pesos chilenos.`);

  const width = 560;
  const height = 280;
  const top = 22;
  const bottom = 52;
  const chartHeight = height - top - bottom;
  const maxCount = Math.max(...bins.map((bin) => bin.count), 1);
  const startX = 36;
  const step = 104;
  const barWidth = 62;
  bins.forEach((bin, index) => {
    const barHeight = bin.count ? bin.count * chartHeight / maxCount : 0;
    const x = startX + index * step;
    const y = top + chartHeight - barHeight;
    svg.append(svgElement('rect', {
      x, y, width: barWidth, height: Math.max(0, barHeight), rx: 3,
      class: index === bins.length - 1 ? 'histogram-bar histogram-bar-near' : 'histogram-bar',
    }));
    svg.append(svgElement('text', {
      x: x + barWidth / 2, y: y - 8, 'text-anchor': 'middle', class: 'chart-value-label',
    }, String(bin.count)));
    svg.append(svgElement('text', {
      x: x + barWidth / 2, y: height - 17, 'text-anchor': 'middle', class: 'chart-axis-label',
    }, bin.label));
  });
}

function renderDashboard(history) {
  if (!core) throw new Error('No se pudo cargar el módulo de precios.');
  const offers = core.currentOffers(history);
  renderOfferTable('s23_fe', offers);
  renderOfferTable('s23', offers);
  renderPriceSummary(history, offers);
  renderPriceAdvice(offers);
  drawTrend(history);
  drawHistogram(offers);
}

async function loadPriceHistory() {
  try {
    const response = await fetch('data/price-history.json', { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    renderDashboard(await response.json());
  } catch {
    const onFileProtocol = window.location.protocol === 'file:';
    const hint = onFileProtocol
      ? 'Abrí la página con ./start-website.sh (o python3 -m http.server 8000): el navegador bloquea la lectura de datos cuando se abre index.html directo.'
      : 'No se pudo leer data/price-history.json. Ejecutá python3 scripts/update_prices.py para poblar el historial.';
    setText('price-update-status', hint);
    setText('trend-empty', hint);
    setText('histogram-empty', hint);
    setText('price-advice', 'Cuando el archivo de precios esté disponible, esta sección comparará los valores capturados y sus variantes de procesador.');
  }
}

loadPriceHistory();
