import {STATUSES} from '../../constants/enums.js';
export const status=s=>`<span class="status ${s}">${STATUSES[s]||s}</span>`;
