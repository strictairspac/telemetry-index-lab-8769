import {workloads,candidates,experiments} from '../api/business.js';
export const store={workloads:[],candidates:[],experiments:[],user:null};
export async function refresh(){[store.workloads,store.candidates,store.experiments]=await Promise.all([workloads.list(),candidates.list(),experiments.list()]);}
