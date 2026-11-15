// Hub product prototype: a clickable sketch on made-up data. Nothing here talks to a server.
// The address picks what is shown, so every state can be opened and photographed:
//   #home | #today | #kitchen | #system
//   ?theme=dusk|day  ?sheet=room:kitchen|playing|ask  ?ask=type|lights|music|tomorrow|tacos|shop
//   ?state=offline|loading|empty|quiet|update
(function () {
  const D = JSON.parse(JSON.stringify(window.DATA));
  const P = new URLSearchParams(location.search);
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const wide = () => matchMedia('(min-width: 1040px)').matches;
  const S = { view: (location.hash || '#home').slice(1), sheet: P.get('sheet') || '', state: P.get('state') || '', q: '', turns: [], last: null };
  document.documentElement.dataset.theme = P.get('theme') || 'dusk';
  if (S.state === 'quiet') D.music.playing = false;

  // ---------- drawings ----------
  const PATHS = {
    more: '<circle cx="5.5" cy="12" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="18.5" cy="12" r="1.5"/>',
    play: '<path d="M8 5.6v12.8a.6.6 0 0 0 .92.5l10-6.4a.6.6 0 0 0 0-1l-10-6.4a.6.6 0 0 0-.92.5z"/>',
    pause: '<rect x="6.5" y="5" width="4" height="14" rx="1.2"/><rect x="13.5" y="5" width="4" height="14" rx="1.2"/>',
    next: '<path d="M5.5 6.2v11.6a.5.5 0 0 0 .78.42L15 12.4a.5.5 0 0 0 0-.84L6.28 5.78a.5.5 0 0 0-.78.42z"/><rect x="16.5" y="5.5" width="2.4" height="13" rx="1.1"/>',
    prev: '<path d="M18.5 6.2v11.6a.5.5 0 0 1-.78.42L9 12.4a.5.5 0 0 1 0-.84l8.72-5.78a.5.5 0 0 1 .78.42z"/><rect x="5.1" y="5.5" width="2.4" height="13" rx="1.1"/>',
    x: '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
    check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    wave: '<path d="M4.5 10.5v3M8.25 7.5v9M12 4.5v15M15.75 8v8M19.5 10.5v3"/>',
    up: '<path d="M12 19V5.5M6 11l6-6 6 6"/>',
    bolt: '<path d="M13 3 5.5 13.5H11L10 21l8-10.5h-5.5z"/>',
  };
  const SOLID = { more: 1, play: 1, pause: 1, next: 1, prev: 1 };
  const ic = (n) => `<svg class="ic${SOLID[n] ? ' solid' : ''}" viewBox="0 0 24 24" aria-hidden="true">${PATHS[n]}</svg>`;
  const eq = '<span class="eq" aria-label="Playing"><i></i><i></i><i></i></span>';
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  // ---------- words ----------
  const N = ['No', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine', 'Ten'];
  const list = (a) => (a.length < 2 ? a.join('') : a.slice(0, -1).join(', ') + ' and ' + a[a.length - 1]);
  const room = (id) => D.rooms.find((r) => r.id === id);
  const playingIn = () => D.music.speakers.filter((s) => s.on).map((s) => s.name.toLowerCase());
  function houseSays() {
    const lit = D.rooms.filter((r) => r.on).length;
    return lit === 0 ? 'The house is dark.' : lit === 1 ? 'One room is lit.' : `${N[lit] || lit} rooms are lit.`;
  }
  function musicSays() {
    const m = D.music, where = playingIn();
    if (!m.playing || !where.length) return 'The house is quiet.';
    return `${m.from} is playing in the ${list(where)}.`;
  }

  // ---------- pieces ----------
  function roomCell(r, opts = {}) {
    const speaker = r.speaker && D.music.playing && D.music.speakers.find((s) => s.id === r.speaker && s.on);
    const cls = ['room', 's' + (opts.span || r.span || 2), r.tall || opts.tall ? 'tall' : '', r.on ? 'lit' : '', r.on && r.level < 35 ? 'low' : ''].filter(Boolean).join(' ');
    const val = r.on ? `<span class="room-num">${r.level}<small>%</small></span>` : '<span></span>';
    return `<div class="${cls}" style="--lvl:${r.level / 100};--i:${D.rooms.filter((x) => x.on).indexOf(r)}" role="switch" aria-checked="${r.on}" tabindex="0" data-room="${r.id}" data-act="room-toggle:${r.id}">
      <div><div class="room-name">${esc(r.name)}</div>${r.note && r.on ? `<div class="room-note">${esc(r.note)}</div>` : ''}</div>
      <div class="room-val">${val}${speaker ? eq : ''}</div>
      <button class="room-more" aria-label="${esc(r.name)}: all controls" data-act="room-open:${r.id}">${ic('more')}</button>
    </div>`;
  }
  const plan = () => D.floors.map((f) => `<p class="floor">${f.name}</p><div class="plan">${f.rooms.map((id) => roomCell(room(id))).join('')}</div>`).join('');
  const top = () => `<div class="top"><span class="when">${D.now.day} ${D.now.part}</span>
    <button class="who${S.state === 'offline' ? ' bad' : ''}" data-act="nav:system" aria-label="System and settings">${D.me.initial}</button></div>`;

  // ---------- screens ----------
  const VIEWS = {
    home() {
      const off = S.state === 'offline';
      return `${top()}
      ${off ? `<div class="banner"><b>Can’t reach the house</b><span>This phone is off the home network. Showing things as they were at 6:31. Turn Tailscale on and Hub will reconnect by itself.</span></div>` : ''}
      <h1 class="say" id="say">${houseSays()}</h1>
      <p class="sub" id="sub">${musicSays()}</p>
      <div class="scenes">${D.scenes.map((s) => `<button class="scene${s.active ? ' on' : ''}" data-act="scene:${s.id}">${esc(s.name)}</button>`).join('')}</div>
      ${plan()}
      <div class="facts">${D.facts.map((f) => `<div class="fact"><span>${f.label}</span><b>${f.value}</b></div>`).join('')}</div>
      <p class="tip">Tap a room to switch its lights. Hold it for everything in the room.</p>`;
    },
    today() {
      const t = D.today;
      if (S.state === 'empty') {
        return `${top()}<div class="empty"><h1 class="say">Nothing needs you.</h1>
        <p class="sub">Tomorrow starts with swim at 9:30. Hub will tell you if that changes.</p></div>`;
      }
      const open = t.needs.filter((n) => !n.done).length;
      const ev = (e) => `<div class="row${e.warn ? ' warn' : ''}${e.now ? ' now' : ''}"><span class="row-t">${e.time}</span><div class="row-main"><b>${esc(e.title)}</b><span>${esc(e.sub)}</span></div></div>`;
      return `${top()}
      <h1 class="say">Dinner at seven. ${open ? `${N[open]} thing${open > 1 ? 's' : ''} need${open > 1 ? '' : 's'} you.` : 'Nothing needs you.'}</h1>
      <p class="sub">${D.weather.temp}° outside. ${D.weather.text}.</p>
      <div class="cols">
      <div>
      <section class="block"><h2>Needs you</h2><div class="rows">
        ${t.needs.map((n) => `<div class="row tick${n.done ? ' done' : ''}" data-need="${n.id}">
          <button class="ring" aria-label="Done" data-act="need:${n.id}">${ic('check')}</button>
          <div class="row-main"><b>${esc(n.title)}</b><span>${esc(n.sub)}</span></div>
          ${n.done ? '' : `<button class="row-act" data-act="${n.id === 'n1' ? 'nav:kitchen' : 'noop'}">${n.act}</button>`}</div>`).join('')}
      </div></section>
      <section class="block"><h2>This evening</h2><div class="rows">${t.next.map(ev).join('')}</div></section>
      </div><div>
      <section class="block"><h2>Tomorrow</h2><div class="rows">${t.tomorrow.map(ev).join('')}</div></section>
      <section class="block"><h2>This week at school</h2><div class="rows">
        ${t.school.map((s) => `<div class="row"><span class="row-t day">${s.day}</span><div class="row-main"><b>${esc(s.title)}</b><span>${esc(s.sub)}</span></div></div>`).join('')}
      </div></section>
      <section class="block"><h2>${esc(t.trip.title)}</h2><div class="rows"><div class="row free"><div class="row-main"><b>In 73 days</b><span>Flights aren’t chosen yet</span></div><button class="row-act" data-act="noop">Compare</button></div></div></section>
      </div></div>
      <p class="quiet">${esc(t.quiet)}</p>`;
    },
    kitchen() {
      const k = D.kitchen, c = k.cart;
      return `${top()}
      <h1 class="say">${c.approved ? 'The order is in.' : 'The cart is ready.'}</h1>
      <p class="sub">${c.count} things, ${c.total}. ${c.slot}, at ${esc(c.store)}.</p>
      ${c.approved ? '' : `<div class="card"><b>One swap to check</b><span>The usual oat milk is out, so Hub picked the other brand you’ve bought before.</span>
      <div class="btns"><button class="btn go" data-act="approve">Approve the cart</button><button class="btn plain" data-act="noop">See all ${c.count}</button></div></div>`}
      <div class="cols"><div>
      <section class="block"><h2>This week’s dinners</h2><div class="rows">
        ${k.meals.map((m) => `<div class="row"><span class="row-t day">${m.day}</span><div class="row-main"><b>${esc(m.name)}</b><span>${m.mins} minutes</span></div></div>`).join('')}
      </div></section></div><div>
      <section class="block"><h2>The list</h2>
        <input class="field" placeholder="Add something" aria-label="Add something to the list">
        ${k.list.map((g, gi) => `<div class="rows" style="margin-top:14px">${g.items.map((it, ii) => `<div class="row tick${it.done ? ' done' : ''}">
          <button class="ring" aria-label="Got it" data-act="item:${gi}:${ii}">${ic('check')}</button>
          <div class="row-main"><b>${esc(it.t)}</b></div><span class="row-q">${esc(it.q)}</span></div>`).join('')}</div>`).join('')}
      </section></div></div>`;
    },
    system() {
      const s = D.system, bad = S.state === 'offline';
      const strip = (c) => `<span class="strip">${Array.from({ length: c.dots }, (_, i) => `<i${(c.miss || []).includes(i) ? ' class="miss"' : ''}></i>`).join('')}</span>`;
      return `${top()}
      <h1 class="say">${bad ? 'The house isn’t answering.' : 'Everything is working.'}</h1>
      <p class="sub">${bad ? 'Lights and speakers stopped answering at 6:31. Mail, calendar and Kitchen are fine.' : 'Hub tested itself against the real house 10 minutes ago.'}</p>
      ${S.state === 'update' || !S.state ? `<div class="card"><b>${s.update.title}</b><span>${s.update.sub}</span><div class="btns"><button class="btn go small" data-act="update">Update Hub</button><button class="btn plain small" data-act="noop">What changed</button></div></div>` : ''}
      <div class="cols"><div>
      <section class="block"><h2>The last 30 days</h2><div class="rows">
        ${s.checks.map((c) => `<div class="check"><b>${c.name}</b>${strip(c)}<span class="why">${c.sub}</span></div>`).join('')}
      </div></section>
      </div><div>
      <section class="block"><h2>Hub remembers</h2><div class="rows">
        ${s.remembers.map((r) => `<div class="row free"><div class="row-main"><b style="font-weight:400">${esc(r)}</b></div><button class="row-q" data-act="noop">Forget</button></div>`).join('')}
      </div></section>
      <section class="block"><h2>People</h2><div class="rows">
        ${D.people.map((p) => `<div class="row free"><div class="row-main"><b>${p.name}</b><span>${p.device}</span></div><span class="row-q">${p.where}</span></div>`).join('')}
      </div></section>
      <section class="block"><h2>Asking</h2><p class="sub">Ask has used $${s.spend.used.toFixed(2)} of $${s.spend.limit} this month.</p><div class="meter" style="--v:${(s.spend.used / s.spend.limit) * 100}"></div></section>
      <section class="block"><h2>Under the hood</h2><div class="rows">
        ${['Everything Hub changed, with undo', 'Home Assistant', 'Music Assistant'].map((x) => `<button class="row free" data-act="noop"><div class="row-main"><b style="font-weight:400">${x}</b></div></button>`).join('')}
      </div></section>
      </div></div>
      <p class="quiet">${s.version}. ${s.updated}.</p>`;
    },
  };

  // ---------- ask ----------
  const SCRIPT = {
    lights: { asked: 'Dim downstairs for dinner', answer: 'Dimmed the kitchen and living room to 40%.', took: 'Done in 0.7 seconds', kind: 'rooms', rooms: ['kitchen', 'living'],
      run() { S.last = snapshot(['kitchen', 'living']); setLevel('kitchen', 40); setLevel('living', 40); } },
    music: { asked: 'Play this in the dining room too', answer: 'Evening Jazz is in the dining room as well, starting softly at 15%.', took: 'Done in 0.9 seconds', kind: 'speakers',
      run() { const s = D.music.speakers.find((x) => x.id === 'dining'); s.on = true; s.vol = 15; } },
    tomorrow: { asked: 'What’s on tomorrow?', answer: 'Three things. The plumber’s call overlaps the dentist at four, so one of them has to move.', kind: 'agenda' },
    tacos: { asked: 'Can we do tacos tonight?', answer: 'Yes. You have the beans and the avocados. Four things are missing, and they fit in tomorrow’s pickup.', kind: 'shop' },
    shop: { asked: 'Add them to the cart', answer: 'Adding four things to the cart at Greenway Market. The shop’s site is slow, so this takes a minute or two. Close this if you like: your phone will buzz when it’s done.', kind: 'work' },
  };
  const DEFAULTS = [
    { text: 'Bedtime', small: 'Night light in Robin’s room, hallway low, the rest off', act: 'scene:bedtime', instant: true },
    { text: 'Dim downstairs for dinner', act: 'ask:lights' },
    { text: 'Play this in the dining room too', act: 'ask:music' },
    { text: 'What’s on tomorrow?', act: 'ask:tomorrow' },
    { text: 'Can we do tacos tonight?', act: 'ask:tacos' },
  ];
  function matches(q) {
    const hit = (s) => s.toLowerCase().includes(q.toLowerCase());
    const mark = (s) => { const i = s.toLowerCase().indexOf(q.toLowerCase()); return esc(s.slice(0, i)) + '<mark>' + esc(s.slice(i, i + q.length)) + '</mark>' + esc(s.slice(i + q.length)); };
    const out = [];
    D.rooms.filter((r) => hit(r.name)).forEach((r) => out.push(`<div class="sug"><span>${mark(r.name)}<small>${r.on ? `Lights on at ${r.level}%` : 'Lights off'}</small></span><button class="switch${r.on ? ' on' : ''}" role="switch" aria-checked="${r.on}" aria-label="${esc(r.name)} lights" data-act="room-toggle:${r.id}" data-sw="${r.id}"></button></div>`));
    D.scenes.filter((s) => hit(s.name)).forEach((s) => out.push(`<button class="sug" data-act="scene:${s.id}"><span>${mark(s.name)}<small>Scene</small></span><span class="go">${ic('bolt')}</span></button>`));
    D.music.picks.filter((p) => hit(p.name)).forEach((p) => out.push(`<button class="sug" data-act="pick:${p.name}"><span>${mark(p.name)}<small>${p.kind}. Plays where music is on now.</small></span><span class="go">${ic('play')}</span></button>`));
    if (hit('Kitchen list')) out.push(`<button class="sug" data-act="nav:kitchen"><span>${mark('Kitchen list')}<small>7 things, 2 already in the house</small></span><span class="how">Open</span></button>`);
    out.push(`<button class="sug" data-act="ask:free"><span>Ask Hub “${esc(q)}”</span><span class="go">${ic('up')}</span></button>`);
    return out.join('');
  }
  function turnHTML(t) {
    let extra = '';
    if (t.kind === 'rooms') extra = `<div class="plan">${t.rooms.map((id) => roomCell(room(id), { span: 3 })).join('')}</div>`;
    if (t.kind === 'speakers') extra = `<div class="rows">${D.music.speakers.filter((s) => s.on).map((s) => `<div class="ctl"><div class="ctl-name"><b>${s.name}</b></div>${slim(s.vol, 'snd', `vol:${s.id}`)}<button class="switch snd on" role="switch" aria-checked="true" aria-label="${s.name}" data-act="speaker:${s.id}"></button></div>`).join('')}</div>`;
    if (t.kind === 'agenda') extra = `<div class="rows">${D.today.tomorrow.map((e) => `<div class="row${e.warn ? ' warn' : ''}"><span class="row-t">${e.time}</span><div class="row-main"><b>${esc(e.title)}</b><span>${esc(e.sub)}</span></div></div>`).join('')}</div><div class="btns"><button class="btn plain small" data-act="noop">Move the plumber</button><button class="btn plain small" data-act="noop">Move the dentist</button></div>`;
    if (t.kind === 'shop') extra = `<div class="rows">${[['Corn tortillas', '1 pack'], ['Limes', '4'], ['Cilantro', '1 bunch'], ['Sour cream', '1 small']].map(([a, b]) => `<div class="row free"><div class="row-main"><b style="font-weight:400">${a}</b></div><span class="row-q">${b}</span></div>`).join('')}</div>
      ${t.added ? '' : '<div class="btns"><button class="btn go small" data-act="ask:shop">Add 4 to the cart</button><button class="btn plain small" data-act="noop">Just the list</button></div>'}`;
    if (t.kind === 'work') extra = `<div class="work"></div><div class="steps"><span class="did">Opened tomorrow’s cart</span><span class="did">Found the tortillas and the limes</span><span>Looking for cilantro</span></div>`;
    if (t.kind === 'think') return `<div class="turn"><p class="asked">${esc(t.asked)}</p><span class="dots"><i></i><i></i><i></i></span></div>`;
    const took = t.took ? `<p class="took"><span>${t.took}</span>${t.kind === 'rooms' ? '<button data-act="undo">Undo</button>' : ''}</p>` : '';
    return `<div class="turn"><p class="asked">${esc(t.asked)}</p><p class="answer${t.kind === 'work' ? ' wait' : ''}">${esc(t.answer)}</p>${extra}${took}</div>`;
  }
  function askHTML() {
    let body;
    if (S.turns.length) body = S.turns.map(turnHTML).join('');
    else if (S.q) body = matches(S.q);
    else body = `<p class="ask-hint">For this evening</p>` + DEFAULTS.map((d) => `<button class="sug" data-act="${d.act}"><span>${esc(d.text)}${d.small ? `<small>${esc(d.small)}</small>` : ''}</span><span class="go">${ic(d.instant ? 'bolt' : 'up')}</span></button>`).join('');
    return `<div class="ask"><div class="ask-scroll"><div>${body}</div></div>
      <form class="composer" data-form="ask"><input class="field" id="askq" autocomplete="off" placeholder="Say it, or type a room" value="${esc(S.q)}" aria-label="Ask Hub">
      <button class="send" aria-label="Send">${ic('up')}</button></form></div>`;
  }
  function ask(key, text) {
    const s = SCRIPT[key] || { asked: text || S.q, answer: 'This prototype knows five requests: dim downstairs, play this in the dining room too, what’s on tomorrow, tacos tonight, and adding to the cart. The real Hub answers anything, with the same cards.', kind: 'text' };
    if (key === 'shop') S.turns.forEach((t) => { if (t.kind === 'shop') t.added = true; });
    S.q = '';
    const fast = P.get('still') === '1';
    const land = () => { if (s.run) { s.run(); D.rooms.forEach((r) => patchRoom(r.id)); } S.turns = S.turns.filter((t) => t.kind !== 'think').concat([{ ...s }]); paintAsk(); paintWords(); };
    if (fast) return land();
    S.turns.push({ kind: 'think', asked: s.asked }); paintAsk();
    setTimeout(land, s.took ? 650 : 1100);
  }

  // ---------- controls ----------
  function slim(v, cls, act, off) {
    return `<div class="slim ${cls || ''}${off ? ' off' : ''}" style="--v:${v}"><b></b><input type="range" min="0" max="100" value="${v}" data-slide="${act}" aria-label="Level"></div>`;
  }
  function setLevel(id, v) { const r = room(id); r.level = v; r.on = v > 0; r.lights.forEach((l) => { if (l.on || !r.lights.some((x) => x.on)) { l.on = v > 0; l.level = v; } }); }
  function snapshot(ids) { return ids.map((id) => JSON.parse(JSON.stringify(room(id)))); }
  function toggleRoom(id) {
    const r = room(id); S.last = snapshot([id]);
    if (r.on) { r.was = r.level; setLevel(id, 0); r.lights.forEach((l) => (l.on = false)); } else { setLevel(id, r.was || 60); r.lights.forEach((l) => { l.on = true; l.level = r.level; }); }
    patchRoom(id); paintWords();
    toast(`${r.name} ${r.on ? 'on' : 'off'}`, true);
  }
  function patchRoom(id) {
    const r = room(id);
    $$(`.room[data-room="${id}"]`).forEach((el) => {
      el.outerHTML = roomCell(r, { span: el.classList.contains('s3') ? 3 : 2, tall: el.classList.contains('tall') });
    });
    $$(`[data-sw="${id}"]`).forEach((el) => { el.classList.toggle('on', r.on); el.setAttribute('aria-checked', r.on); const sm = el.parentElement.querySelector('small'); if (sm) sm.textContent = r.on ? `Lights on at ${r.level}%` : 'Lights off'; });
  }
  function paintWords() { const a = $('#say'), b = $('#sub'); if (a && S.view === 'home') { a.textContent = houseSays(); b.textContent = musicSays(); } paintDock(); }

  // ---------- sheets ----------
  function roomSheet(id) {
    const r = room(id), on = r.lights.filter((l) => l.on).length;
    const sp = r.speaker && D.music.speakers.find((s) => s.id === r.speaker);
    return `<div class="grab"></div>
    <div class="sheet-head"><div><h1>${esc(r.name)}</h1><p id="roomsay">${on ? `${N[on]} light${on > 1 ? 's' : ''} on.` : 'All lights are off.'}</p></div><button class="x" data-act="close" aria-label="Close">${ic('x')}</button></div>
    <div class="dimmer${r.on ? '' : ' zero'}" style="--v:${r.level}"><div class="dimmer-fill"></div><span class="dimmer-num">${r.on ? r.level + '%' : 'Off'}</span><input type="range" min="0" max="100" value="${r.level}" data-slide="room:${id}" aria-label="${esc(r.name)} brightness"></div>
    <div class="warmth">${[['Candle', '#FFB25E'], ['Warm', '#FFD9A6'], ['Daylight', '#F2F6FF']].map(([n, c], i) => `<button class="warm${i === 1 ? ' on' : ''}" style="--c:${c}" data-act="warm"><i></i>${n}</button>`).join('')}</div>
    <h2>Lights</h2>
    ${r.lights.map((l, i) => `<div class="ctl"><div class="ctl-name"><b>${esc(l.name)}</b></div>${slim(l.level, '', `light:${id}:${i}`, !l.on)}<button class="switch${l.on ? ' on' : ''}" role="switch" aria-checked="${l.on}" aria-label="${esc(l.name)}" data-act="light:${id}:${i}"></button></div>`).join('')}
    ${sp ? `<h2>Speaker</h2><div class="ctl"><div class="ctl-name"><b>${sp.on && D.music.playing ? esc(D.music.from) : 'Not playing'}</b><span>${sp.on ? 'Volume ' + sp.vol + '%' : 'Tap to join what’s playing'}</span></div>${slim(sp.vol, 'snd', `vol:${sp.id}`, !sp.on)}<button class="switch snd${sp.on ? ' on' : ''}" role="switch" aria-checked="${sp.on}" aria-label="Speaker" data-act="speaker:${sp.id}"></button></div>` : ''}
    ${r.plugs.length ? `<h2>Also here</h2>${r.plugs.map((p, i) => `<div class="ctl two"><div class="ctl-name"><b>${esc(p.name)}</b><span>${esc(p.note)}</span></div><button class="switch${p.on ? ' on' : ''}" role="switch" aria-checked="${p.on}" aria-label="${esc(p.name)}" data-act="plug:${id}:${i}"></button></div>`).join('')}` : ''}`;
  }
  function playingSheet() {
    const m = D.music;
    return `<div class="grab"></div>
    <button class="x float" data-act="close" aria-label="Close">${ic('x')}</button>
    <div class="playing"><div class="nowp"><div class="sleeve a"></div>
    <div class="track"><b>${esc(m.title)}</b><span>${esc(m.artist)}<br>From ${esc(m.from)}</span></div></div>
    <div class="scrub" style="--v:${m.pos * 100}"></div><div class="times"><span>${m.at}</span><span>${m.left}</span></div>
    <div class="transport"><button aria-label="Back">${ic('prev')}</button><button class="big" data-act="play" aria-label="${m.playing ? 'Pause' : 'Play'}">${ic(m.playing ? 'pause' : 'play')}</button><button aria-label="Next">${ic('next')}</button></div></div>
    <h2>Where it plays</h2>
    ${m.speakers.map((s) => `<div class="ctl"><div class="ctl-name"><b>${esc(s.name)}</b>${s.note ? `<span>${esc(s.note)}</span>` : ''}</div>${slim(s.vol, 'snd', `vol:${s.id}`, !s.on)}<button class="switch snd${s.on ? ' on' : ''}" role="switch" aria-checked="${s.on}" aria-label="${esc(s.name)}" data-act="speaker:${s.id}"></button></div>`).join('')}
    <h2>Play something else</h2>
    <div class="picks">${m.picks.map((p) => `<button class="pick" data-act="pick:${p.name}"><div class="sleeve ${p.art} mid"></div><b>${esc(p.name)}</b><span>${p.kind}</span></button>`).join('')}</div>`;
  }
  function paintSheet() {
    const el = $('#sheet'), sc = $('#scrim');
    let html = '';
    const [kind, arg] = S.sheet.split(':');
    if (kind === 'room' && room(arg)) html = roomSheet(arg);
    else if (kind === 'playing') html = playingSheet();
    else if (kind === 'ask' && !wide()) html = `<div class="grab"></div>${askHTML()}`;
    if (!html) { el.classList.remove('in'); sc.classList.remove('in'); setTimeout(() => { if (!S.sheet) { el.hidden = true; sc.hidden = true; } }, 340); return; }
    el.innerHTML = html; el.classList.toggle('tall', kind === 'ask'); el.hidden = false; sc.hidden = false;
    requestAnimationFrame(() => requestAnimationFrame(() => { el.classList.add('in'); sc.classList.add('in'); }));
  }
  function paintAsk() {
    if (wide()) { $('#askrail').innerHTML = `<h2>Ask</h2>${askHTML()}`; }
    else if (S.sheet === 'ask') { const el = $('#sheet'); const keep = document.activeElement && document.activeElement.id === 'askq'; el.innerHTML = `<div class="grab"></div>${askHTML()}`; if (keep) { const q = $('#askq'); q.focus(); q.setSelectionRange(q.value.length, q.value.length); } }
    const sc = $('.ask-scroll'); if (sc) sc.scrollTop = sc.scrollHeight;
  }

  // ---------- frame ----------
  const TABS = [['home', 'Home'], ['today', 'Today'], ['kitchen', 'Kitchen']];
  function paintDock() {
    const m = D.music, where = playingIn();
    const last = list(D.music.speakers.filter((s) => s.on).map((s) => s.name.toLowerCase()));
    const np = `<div class="np${m.playing ? ' live' : ''}" role="button" tabindex="0" data-act="open-playing"><div class="sleeve a"></div>
      <div class="np-text"><b>${esc(m.playing ? m.title : m.from)}</b><span>${m.playing ? eq + cap(list(where)) : 'Play again in the ' + last}</span></div>
      <button class="np-play" data-act="play" aria-label="${m.playing ? 'Pause' : 'Play'}">${ic(m.playing ? 'pause' : 'play')}</button></div>`;
    $('#dock').innerHTML = `${np}<div class="tabs">${TABS.map(([id, n]) => `<button class="tab${S.view === id ? ' on' : ''}" data-act="nav:${id}">${n}</button>`).join('')}
      <button class="askbtn" data-act="ask-open">${ic('wave')}Ask</button></div>`;
  }
  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
  function paint() {
    const main = $('#main');
    main.className = 'main' + (S.state === 'offline' ? ' offline' : '') + (S.state === 'loading' ? ' skel' : '');
    main.innerHTML = `<div class="wrap">${(VIEWS[S.view] || VIEWS.home)()}</div>`;
    main.scrollTop = 0; window.scrollTo(0, 0);
    $('#rail').innerHTML = `<div class="mark"><i></i>Hub</div>${TABS.map(([id, n]) => `<button class="tab${S.view === id ? ' on' : ''}" data-act="nav:${id}">${n}</button>`).join('')}
      <div class="foot"><button class="tab${S.view === 'system' ? ' on' : ''}" data-act="nav:system">System</button><p class="ok">${S.state === 'offline' ? 'The house isn’t answering.' : 'Everything is working. Checked 10 minutes ago.'}</p></div>`;
    paintDock(); paintAsk(); paintSheet();
  }
  let toastT;
  function toast(text, undo) {
    const el = $('#toast');
    el.innerHTML = `<span>${esc(text)}</span>${undo ? '<button data-act="undo">Undo</button>' : '<span></span>'}`;
    el.hidden = false; requestAnimationFrame(() => el.classList.add('in'));
    clearTimeout(toastT); toastT = setTimeout(() => { el.classList.remove('in'); }, 4200);
  }

  // ---------- taps ----------
  const ACT = {
    nav(v) { S.view = v; S.sheet = ''; history.replaceState(null, '', location.pathname + location.search + '#' + v); paint(); },
    'room-toggle'(id) { toggleRoom(id); },
    'room-open'(id) { S.sheet = 'room:' + id; paintSheet(); },
    'open-playing'() { S.sheet = 'playing'; paintSheet(); },
    'ask-open'() { if (wide()) { $('#askq').focus(); return; } S.sheet = 'ask'; paintSheet(); },
    close() { S.sheet = ''; paintSheet(); },
    scene(id) {
      S.last = snapshot(D.rooms.map((r) => r.id));
      D.scenes.forEach((s) => (s.active = s.id === id));
      const set = { dinner: { kitchen: 80, living: 45, dining: 60 }, movie: { kitchen: 0, living: 15, dining: 0, entry: 0 }, bedtime: { robin: 12, hall: 20, kitchen: 30, living: 30, dining: 0, entry: 0, office: 0 }, leaving: {}, alloff: {} }[id];
      if (id === 'alloff' || id === 'leaving') D.rooms.forEach((r) => { if (r.id !== 'porch') setLevel(r.id, 0); }); else Object.entries(set).forEach(([k, v]) => setLevel(k, v));
      if (id === 'leaving') { D.music.playing = false; D.facts[0].value = 'Away'; }
      if (S.sheet === 'ask' && !S.turns.length) S.sheet = '';
      paint(); toast(`${D.scenes.find((s) => s.id === id).name} is on`, true);
    },
    play() { D.music.playing = !D.music.playing; paintWords(); if (S.sheet === 'playing') paintSheet(); $$('.plan').forEach(() => D.rooms.forEach((r) => patchRoom(r.id))); },
    speaker(id) { const s = D.music.speakers.find((x) => x.id === id); s.on = !s.on; if (s.on && !D.music.playing) D.music.playing = true; paintWords(); D.rooms.forEach((r) => patchRoom(r.id)); if (S.sheet) paintSheet(); paintAsk(); },
    light(id, i) { const r = room(id), l = r.lights[i]; l.on = !l.on; if (l.on && !l.level) l.level = 60; const on = r.lights.filter((x) => x.on); r.on = on.length > 0; r.level = r.on ? Math.round(on.reduce((a, x) => a + x.level, 0) / on.length) : 0; patchRoom(id); paintWords(); paintSheet(); },
    plug(id, i) { const p = room(id).plugs[i]; p.on = !p.on; p.note = p.on ? 'On just now' : 'Off just now'; paintSheet(); },
    warm() { $$('.warm').forEach((b) => b.classList.toggle('on', b === event.target.closest('.warm'))); },
    pick(name) { D.music.from = name; D.music.title = name === 'Evening Jazz' ? 'Paper Lanterns' : 'First track'; D.music.playing = true; S.q = ''; paintWords(); if (S.sheet === 'playing') paintSheet(); else { S.sheet = ''; paintSheet(); } toast(`Playing ${name}`); },
    need(id) { const n = D.today.needs.find((x) => x.id === id); n.done = !n.done; paint(); },
    item(g, i) { const it = D.kitchen.list[g].items[i]; it.done = !it.done; paint(); },
    approve() { D.kitchen.cart.approved = true; D.today.needs[0].done = true; paint(); toast('Cart approved. Pickup tomorrow at 11.'); },
    update() { toast('Updating. Hub will be back in about 20 seconds.'); },
    ask(key) { if (!wide() && S.sheet !== 'ask') { S.sheet = 'ask'; paintSheet(); } ask(key); },
    undo() { if (!S.last) return; S.last.forEach((snap) => Object.assign(room(snap.id), snap)); S.last = null; D.rooms.forEach((r) => patchRoom(r.id)); paintWords(); $('#toast').classList.remove('in'); if (S.sheet.startsWith('room')) paintSheet(); },
    noop() {},
  };
  document.addEventListener('click', (e) => {
    const el = e.target.closest('[data-act]');
    if (e.target.id === 'scrim') return ACT.close();
    if (!el) return;
    if (held && el.dataset.act.startsWith('room-toggle')) { held = false; return; }
    const [name, ...args] = el.dataset.act.split(':');
    window.event = e;
    if (ACT[name]) { e.preventDefault(); e.stopPropagation(); ACT[name](...args); }
  });
  // hold a room to open it
  let hold, held = false;
  document.addEventListener('pointerdown', (e) => {
    const el = e.target.closest('.room'); if (!el || e.target.closest('.room-more')) return;
    held = false; el.classList.add('held');
    hold = setTimeout(() => { held = true; el.classList.remove('held'); ACT['room-open'](el.dataset.room); }, 430);
  });
  const letGo = () => { clearTimeout(hold); $$('.room.held').forEach((el) => el.classList.remove('held')); };
  ['pointerup', 'pointercancel', 'pointermove', 'scroll'].forEach((n) => document.addEventListener(n, (e) => { if (n !== 'pointermove' || Math.abs(e.movementX) + Math.abs(e.movementY) > 6) letGo(); }, true));
  document.addEventListener('contextmenu', (e) => { if (e.target.closest('.room')) e.preventDefault(); });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && S.sheet) ACT.close();
    if ((e.key === 'Enter' || e.key === ' ') && e.target.matches('.room, .np')) { e.preventDefault(); e.target.click(); }
    if (e.key === 'k' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); ACT['ask-open'](); }
  });
  document.addEventListener('input', (e) => {
    if (e.target.id === 'askq') { S.q = e.target.value.trim(); S.turns = []; const sc = $('.ask-scroll > div'); sc.innerHTML = S.q ? matches(S.q) : `<p class="ask-hint">For this evening</p>` + DEFAULTS.map((d) => `<button class="sug" data-act="${d.act}"><span>${esc(d.text)}${d.small ? `<small>${esc(d.small)}</small>` : ''}</span><span class="go">${ic(d.instant ? 'bolt' : 'up')}</span></button>`).join(''); return; }
    const sl = e.target.dataset.slide; if (!sl) return;
    const v = +e.target.value, [kind, a, b] = sl.split(':');
    e.target.parentElement.style.setProperty('--v', v);
    if (kind === 'room') { setLevel(a, v); const d = e.target.parentElement; d.classList.toggle('zero', v === 0); $('.dimmer-num', d).textContent = v ? v + '%' : 'Off'; patchRoom(a); paintWords(); }
    if (kind === 'light') { const r = room(a), l = r.lights[b]; l.level = v; l.on = v > 0; const on = r.lights.filter((x) => x.on); r.on = on.length > 0; r.level = r.on ? Math.round(on.reduce((s, x) => s + x.level, 0) / on.length) : 0; patchRoom(a); paintWords(); }
    if (kind === 'vol') { D.music.speakers.find((s) => s.id === a).vol = v; }
  });
  document.addEventListener('submit', (e) => {
    if (e.target.dataset.form !== 'ask') return; e.preventDefault();
    const q = $('#askq').value.trim(); if (!q) return;
    const key = /dim|downstairs|dinner light/i.test(q) ? 'lights' : /dining|too|as well/i.test(q) ? 'music' : /tomorrow|calendar|what.s on/i.test(q) ? 'tomorrow' : /taco|dinner|cook/i.test(q) ? 'tacos' : /cart|add them/i.test(q) ? 'shop' : 'free';
    ask(key, q);
  });
  window.addEventListener('hashchange', () => { S.view = (location.hash || '#home').slice(1); paint(); });
  window.addEventListener('resize', () => { paintAsk(); });

  // a state named in the address, for screenshots
  const pre = P.get('ask');
  if (pre === 'type') S.q = 'kit';
  else if (pre === 'shop') { S.turns = [{ ...SCRIPT.tacos, added: true }, { ...SCRIPT.shop }]; }
  else if (pre && SCRIPT[pre]) { if (SCRIPT[pre].run) SCRIPT[pre].run(); S.turns = [{ ...SCRIPT[pre] }]; }
  if (pre && !wide()) S.sheet = 'ask';
  paint();
  if (P.get('still') !== '1' && !S.state) { $('#main').classList.add('wake'); setTimeout(() => $('#main').classList.remove('wake'), 1800); }
})();
