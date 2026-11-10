import fs from 'node:fs/promises';
const boxes=[['@ mention / reply','群里 @ 或回复'],['Shared group context','读取共享上下文'],['GitHub / Linear','按授权只读查询'],['Answer in the group','在群里回答']];
const svg=`<svg xmlns="http://www.w3.org/2000/svg" width="1120" height="240" viewBox="0 0 1120 240" role="img" aria-label="Illustration of group context and read-only sources, with a separate daily digest">
<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10" fill="#718299"/></marker></defs>
<rect width="1120" height="240" rx="14" fill="#101c2d"/>
<g font-family="system-ui,sans-serif"><text x="24" y="31" fill="#a7bbd2" font-size="14">ILLUSTRATION · 示意图 · Scoped sources are opt-in / 数据源须显式启用</text>
${boxes.map(([en,zh],i)=>`<rect x="${24+i*278}" y="53" width="238" height="80" rx="10" fill="#1d3046" stroke="#355773"/><text x="${143+i*278}" y="84" text-anchor="middle" fill="#f1f6fc" font-size="18">${en}</text><text x="${143+i*278}" y="112" text-anchor="middle" fill="#a5cfdf" font-size="17">${zh}</text>${i<3?`<path d="M${266+i*278} 93h28" stroke="#718299" stroke-width="2" marker-end="url(#arrow)"/>`:''}`).join('')}
<path d="M24 157h1072" stroke="#355773"/>
<text x="30" y="188" fill="#8bd5bd" font-size="18">Daily digest / 每日摘要</text><text x="310" y="188" fill="#e1eaf6" font-size="18">Followed accounts + search → budget guard → summary to group</text>
<text x="310" y="216" fill="#a7bbd2" font-size="16">关注与搜索 → 预算限制 → 摘要发到群里 · Not a live delivery receipt / 非真实投递凭证</text></g></svg>`;
await fs.writeFile(new URL('./group-flow.svg',import.meta.url),svg);
