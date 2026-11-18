import {esc} from '../../utils/html.js';
export function table(head,rows){return rows.length?`<div class=table-wrap><table><thead><tr>${head.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${r.map(c=>`<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`:'<div class=empty>还没有记录。先在左侧录入输入，再创建一次试验。</div>';};
