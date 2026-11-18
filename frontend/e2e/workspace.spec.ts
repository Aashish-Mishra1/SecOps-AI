import {test,expect,type Page} from '@playwright/test';
async function login(page:Page,role:string){
  await page.getByLabel('Email',{exact:true}).fill(role+'@secops.example');
  await page.getByLabel('Password').fill(process.env.DEMO_PASSWORD!);
  await page.getByRole('button',{name:'Sign in'}).click();
}
test('analyst investigates and reviewer approves cited report',async({page})=>{
  await page.goto('/');await login(page,'analyst');
  await page.getByRole('row').filter({hasText:'Repeated authentication failures'}).first().getByText('Investigate').click();
  const state=page.locator('section > p').filter({hasText:'awaiting_review'});
  await expect(state).toBeVisible();const id=(await state.innerText()).split(' ')[0];
  await page.getByText('Sign out').click();await login(page,'reviewer');
  await page.getByRole('link',{name:'Investigations',exact:true}).click();
  await page.getByRole('row').filter({hasText:id}).getByText('Inspect').click();
  await page.getByRole('button',{name:'approve',exact:true}).click();
  await expect(page.locator('section > p').filter({hasText:id})).toContainText('approved');
});
