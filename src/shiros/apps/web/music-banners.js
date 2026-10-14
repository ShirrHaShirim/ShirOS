/* Image templates follow explicit identities; this registry is open to additions. */
(() => {
  const roles = {
    '作曲家':'composer','指挥家':'conductor','演奏家':'performer',
    '歌唱家':'singer','歌手':'singer',
    '主音吉他':'guitar','节奏吉他':'guitar','吉他手':'guitar',
    '贝斯':'bass','贝斯手':'bass','鼓':'drums','鼓手':'drums',
    '键盘':'keyboard','键盘手':'keyboard',
    '小提琴':'violin','中提琴':'viola','大提琴':'cello',
  };
  const instruments = new Set(['guitar','bass','drums','keyboard','violin','viola','cello']);
  const partPresets = {};
  const ensembleParts=new Set();
  function setParts(catalogue){for(const [group,parts] of Object.entries(catalogue.groups))for(const part of parts){
    if(catalogue.parts[part].role==='ensemble'){ensembleParts.add(part);continue;}
    if(roles[part])continue;
    if(catalogue.parts[part].role==='singer'){roles[part]='singer';continue;}
    const key=`part:${part}`;roles[part]=key;
    partPresets[key]=group==='声部分工'?'singer':group==='键盘乐器'?'keyboard':
      ['电贝斯'].includes(part)?'bass':['吉他','电吉他','原声吉他','古典吉他'].includes(part)?'guitar':
      ['鼓','架子鼓','电子打击乐'].includes(part)?'drums':'performer';
    if(catalogue.parts[part].role==='performer')instruments.add(key);
  }}
  const combinations = {
    'composer+conductor':'composer-conductor',
    'composer+conductor+keyboard':'composer-conductor',
    'composer+conductor+performer':'composer-conductor',
    'guitar+singer':'singer-guitar','keyboard+singer':'singer-keyboard',
    'composer+guitar+singer':'singer-guitar',
    'composer+keyboard+singer':'singer-keyboard',
    'composer+singer':'composer-singer','composer+performer':'composer-performer',
  };
  const dark = new Set(['band','performer','drums','bass','rare-dark']);
  const presets = ['composer','conductor','performer','singer','band','orchestra','label',
    'musician','composer-conductor',...instruments,'singer-guitar','singer-keyboard',
    'composer-performer','composer-singer','rare-dark'];
  function resolve(item) {
    const names = new Set((item.identities || []).map(identity=>identity.name));
    let preset;
    if (item.kind==='organization') preset='label';
    else if (item.kind==='group') {
      const forms=[...names].filter(name=>ensembleParts.has(name));
      const bands=new Set(['摇滚乐队','流行乐队','爵士乐队','大乐队','电子音乐组合']);
      preset=forms.length>1?'rare-dark':forms.length===1?(bands.has(forms[0])?'band':'orchestra'):names.has('乐队')?'band':'orchestra';
    }
    else {
      const active = new Set([...names].map(name=>roles[name]).filter(Boolean));
      // A specific performance role supersedes its generic parent, not other identities.
      if ([...active].some(role=>instruments.has(role))) active.delete('performer');
      if([...active].some(role=>partPresets[role]==='singer'))active.delete('singer');
      const key = [...active].sort().join('+');
      const familyKey=[...active].map(role=>['keyboard','guitar','bass','drums'].includes(partPresets[role])?partPresets[role]:role).sort().join('+');
      if (active.size===0) preset='musician';
      else if (active.size===1) preset=partPresets[[...active][0]]||[...active][0];
      else if (combinations[key]) preset=combinations[key];
      else if (combinations[familyKey]) preset=combinations[familyKey];
      else if (active.size===2 && active.has('composer') &&
               [...active].some(role=>instruments.has(role))) preset='composer-performer';
      else preset='rare-dark';
    }
    return {preset,tone:dark.has(preset)?'dark':'light'};
  }
  window.ShirosMusicBanners = {resolve,presets,setParts};
})();
