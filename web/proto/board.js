// One page that frames the prototype's main states side by side, for a single overview picture.
(function () {
  const P = new URLSearchParams(location.search);
  const theme = P.get('theme') || 'dusk';
  const set = P.get('set') || 'phone';
  document.body.classList.toggle('day', theme === 'day');
  const PHONE = [
    ['#home', '', 'Home', 'The house as a plan. Lit rooms are lit. Tap a room to switch it.'],
    ['#home', 'sheet=room:kitchen', 'A room', 'Hold a room for its dimmer, each light, the speaker and plugs.'],
    ['#home', 'sheet=playing', 'Music', 'Where it plays and how loud, before anything else.'],
    ['#home', 'ask=type', 'Ask, typing', 'Type three letters and switch the room right in the list. No waiting.'],
    ['#home', 'ask=lights', 'Ask, in words', 'Say it. The house changes behind the answer, and Undo is one tap.'],
    ['#home', 'ask=tacos', 'Ask about dinner', 'Kitchen answers inside Ask, with the list and one button.'],
    ['#today', '', 'Today', 'What needs you, then the day. Nothing you have to go and check.'],
    ['#kitchen', '', 'Kitchen', 'The cart to approve, the week’s dinners and the shared list.'],
    ['#system', '', 'System', 'Hub tests itself against the real house and shows 30 days of it.'],
    ['#home', 'state=offline', 'When it can’t reach home', 'Says what is wrong and what to do, and shows the last known state.'],
  ];
  const WIDE = [
    ['#home', 'ask=lights', 'On a laptop', 'The same plan, with Ask always open on the right.'],
    ['#today', '', 'Today on a laptop', ''],
  ];
  const items = set === 'wide' ? WIDE : PHONE;
  document.getElementById('board').innerHTML = items.map(([hash, q, title, text]) =>
    `<figure class="${set === 'wide' ? 'wide' : ''}"><div class="frame"><iframe src="index.html?theme=${theme}&still=1${q ? '&' + q : ''}${hash}" title="${title}"></iframe></div>
     <figcaption><b>${title}</b><span>${text}</span></figcaption></figure>`).join('');
})();
