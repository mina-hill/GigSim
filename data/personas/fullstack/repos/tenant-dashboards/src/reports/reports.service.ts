import { Injectable } from "@nestjs/common";
import { DataSource } from "typeorm";

@Injectable()
export class ReportsService {
  constructor(private readonly db: DataSource) {}

  async forTenant(tenantId: string) {
    return this.db.transaction(async (tx) => {
      await tx.query("SELECT set_config('app.tenant_id', $1, true)", [tenantId]);
      return tx.query("SELECT * FROM reports ORDER BY created_at DESC LIMIT 100");
    });
  }
}
