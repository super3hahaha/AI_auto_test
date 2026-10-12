// 失败重跑的纯计划层：历史失败事实（RerunPlan）与本次实际执行分配分开保存。
// 这里不碰 Vue 状态和设备 API，便于独立验证“原设备优先 / 离线手动替代 / 同格去重”。

export interface RerunPlan {
  cases: string[]; // 失败用例 case_id，按用例库原始顺序
  serialsByCase: Record<string, string[]>; // caseId → 原失败设备 serial
  labels: Record<string, string>; // 原失败设备 serial → 当时的展示名
  sourceRecordId?: string; // 来源执行记录 id；旧记录/非历史来源可能没有
  sourceRunId?: string; // 来源看板轮次 id；旧记录可能没有
}

export interface RerunFailureSource {
  serial: string;
  label: string;
}

// 一格实际执行任务对应的失败来源。多个来源映射到同一 targetSerial 时只保留一条 assignment。
export interface RerunAssignment {
  caseId: string;
  targetSerial: string;
  sources: RerunFailureSource[];
  manual?: boolean; // 确认后又在场景库调整到新设备；来源只能按该用例整体保留
}

export interface ResolvedRerunPlan {
  cases: string[];
  serialsByCase: Record<string, string[]>;
  assignments: RerunAssignment[];
  sourceRecordId?: string;
  sourceRunId?: string;
  failureCount: number; // 原失败格数量
  executionCount: number; // 去重后的实际执行格数量
  mergedCount: number; // failureCount - executionCount
}

export function rerunOriginalSerials(plan: RerunPlan): string[] {
  const seen = new Set<string>();
  const serials: string[] = [];
  for (const caseId of plan.cases) {
    for (const serial of plan.serialsByCase[caseId] ?? []) {
      if (seen.has(serial)) continue;
      seen.add(serial);
      serials.push(serial);
    }
  }
  return serials;
}

export function missingRerunSerials(plan: RerunPlan, onlineSerials: Iterable<string>): string[] {
  const online = new Set(onlineSerials);
  return rerunOriginalSerials(plan).filter((serial) => !online.has(serial));
}

/**
 * 在线原设备始终映射回自己；只有离线原设备才读取 replacements。
 * 同一 case 的多个失败来源若映射到同一目标设备，会合并来源并只生成一个执行格。
 */
export function resolveRerunPlan(
  plan: RerunPlan,
  onlineSerials: Iterable<string>,
  replacements: Record<string, string>
): ResolvedRerunPlan {
  const online = new Set(onlineSerials);
  const serialsByCase: Record<string, string[]> = {};
  const assignments: RerunAssignment[] = [];
  let failureCount = 0;

  for (const caseId of plan.cases) {
    const grouped = new Map<string, RerunFailureSource[]>();
    const seenSources = new Set<string>();
    for (const sourceSerial of plan.serialsByCase[caseId] ?? []) {
      // 历史数据若意外重复，也只算一个失败格，避免重复来源污染合并计数。
      if (seenSources.has(sourceSerial)) continue;
      seenSources.add(sourceSerial);
      failureCount++;

      const targetSerial = online.has(sourceSerial) ? sourceSerial : replacements[sourceSerial];
      if (!targetSerial || !online.has(targetSerial)) {
        throw new Error(`原失败设备 ${plan.labels[sourceSerial] || sourceSerial} 尚未选择在线替代设备`);
      }
      const sources = grouped.get(targetSerial) ?? [];
      sources.push({ serial: sourceSerial, label: plan.labels[sourceSerial] || sourceSerial });
      grouped.set(targetSerial, sources);
    }

    serialsByCase[caseId] = [...grouped.keys()];
    for (const [targetSerial, sources] of grouped) {
      assignments.push({ caseId, targetSerial, sources });
    }
  }

  return {
    cases: [...plan.cases],
    serialsByCase,
    assignments,
    sourceRecordId: plan.sourceRecordId,
    sourceRunId: plan.sourceRunId,
    failureCount,
    executionCount: assignments.length,
    mergedCount: failureCount - assignments.length,
  };
}
