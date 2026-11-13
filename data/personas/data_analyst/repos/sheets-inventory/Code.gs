function checkLowStock() {
  const sheet = SpreadsheetApp.getActive().getSheetByName("Inventory");
  const rows = sheet.getDataRange().getValues().slice(1);
  const low = rows.filter(r => r[2] < r[3]).map(r => `${r[0]}: ${r[2]} left`);
  if (low.length) MailApp.sendEmail("coordinator@example.org", "Low stock", low.join("\n"));
}
