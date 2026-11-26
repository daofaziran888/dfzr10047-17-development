import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    {
      path: '/impact-scope',
      name: 'impact-scope-alias',
      component: WorkbenchView,
      props: { moduleKey: 'impact_scope' },
    },
    {
      path: '/impact-scopes',
      name: 'impact-scopes-alias',
      component: WorkbenchView,
      props: { moduleKey: 'impact_scope' },
    },
    {
      path: '/impact-orders',
      name: 'impact-orders-alias',
      component: WorkbenchView,
      props: { moduleKey: 'impact_scope' },
    },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
