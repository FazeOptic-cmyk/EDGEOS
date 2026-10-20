import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

describe("private workflow API contract", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubEnv("NEXT_PUBLIC_APP_MODE", "demo");
    vi.stubEnv("NEXT_PUBLIC_API_URL", "/api/edgeos");
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  it("sends entered paper allocation and idempotency without changing numeric strings", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      id:"p1",opportunity_id:"o1",title:"A vs B",strategy:"arbitrage",sport:"football",status:"open",
      planned_allocation:"37.25",committed_capital:"0",realized_pnl:"0",unrealized_pnl:"0",worst_case_exposure:"37.25",
      notes:"my note",created_at:"2026-10-19T00:00:00Z",opened_at:null,settled_at:null,is_demo:true,legs:[],
    }), { status: 201, headers: { "Content-Type":"application/json", "X-EDGEOS-Mode":"demo" } }));
    vi.stubGlobal("fetch", fetchMock);
    const { createPaperPosition } = await import("./workflow-api");
    const result = await createPaperPosition({ opportunity_id:"o1", total_capital:"37.25", notes:"my note", idempotency_key:"idem-1" });
    expect(result.planned_allocation).toBe("37.25");
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toMatchObject({ opportunity_id:"o1", total_capital:"37.25", notes:"my note" });
    expect(new Headers(init.headers).get("X-Idempotency-Key")).toBe("idem-1");
    expect(new Headers(init.headers).get("X-EDGEOS-Client-Mode")).toBe("demo");
    expect(new Headers(init.headers).has("Authorization")).toBe(false);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/edgeos/api/v1/portfolio/positions");
  });

  it("blocks a runtime mode mismatch before using the response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ items:[] }), {
      status:200,headers:{"Content-Type":"application/json","X-EDGEOS-Mode":"live"},
    })));
    const { getWatchlist } = await import("./workflow-api");
    await expect(getWatchlist()).rejects.toMatchObject({ code:"mode_mismatch" });
  });

  it("passes explicit UTC analytics bounds to the server", async () => {
    const fetchMock=vi.fn().mockResolvedValue(new Response(JSON.stringify({
      mode:"demo",simulation_only:true,insufficient_data:true,from:"2026-10-01T00:00:00Z",to:"2026-10-02T23:59:59Z",
      summary:{realized_pnl:"0",unrealized_pnl:"0",net_roi:"0",fees:"0",turnover:"0",reserved_capital:"0",maximum_drawdown:null,captured_opportunities:0,attempted_opportunities:0},
      equity:[],strategy:[],provider:[],sport:[],market:[],quote_age:[],
    }),{status:200,headers:{"Content-Type":"application/json","X-EDGEOS-Mode":"demo"}}));
    vi.stubGlobal("fetch",fetchMock);
    const{getAnalytics}=await import("./workflow-api");
    await getAnalytics("2026-10-01T00:00:00Z","2026-10-02T23:59:59Z");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/edgeos/api/v1/analytics?from=2026-10-01T00%3A00%3A00Z&to=2026-10-02T23%3A59%3A59Z");
  });

  it("sends and parses conjunctive alert eligibility gates without changing ratio semantics", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      id:"alert-1",name:"Executable edge",condition:"new_verified_opportunity",threshold:null,
      minimum_net_profit:"12.50",minimum_net_roi:"0.02",minimum_liquidity:"250",
      maximum_quote_age_seconds:45,enabled:true,cooldown_seconds:300,last_triggered_at:null,is_demo:true,
    }), { status:201,headers:{"Content-Type":"application/json","X-EDGEOS-Mode":"demo"} }));
    vi.stubGlobal("fetch", fetchMock);
    const { createAlert } = await import("./workflow-api");

    await expect(createAlert({
      name:"Executable edge",condition:"new_verified_opportunity",threshold:null,
      minimum_net_profit:"12.50",minimum_net_roi:"0.02",minimum_liquidity:"250",
      maximum_quote_age_seconds:45,enabled:true,cooldown_seconds:300,idempotency_key:"alert-idem-1",
    })).resolves.toMatchObject({ minimum_net_profit:"12.50", minimum_net_roi:"0.02", maximum_quote_age_seconds:45 });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/edgeos/api/v1/alerts");
    expect(JSON.parse(String(init.body))).toMatchObject({
      minimum_net_profit:"12.50",minimum_net_roi:"0.02",minimum_liquidity:"250",maximum_quote_age_seconds:45,
    });
  });

  it("updates validated preferences through the authenticated settings API", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      timezone:"UTC",currency:"USD",default_allocation:"375.5",
      maximum_quote_age_seconds:120,demo_mode:true,verified_matches_only:false,
    }), { status:200,headers:{"Content-Type":"application/json","X-EDGEOS-Mode":"demo"} }));
    vi.stubGlobal("fetch", fetchMock);
    const { updateUserSettings } = await import("./workflow-api");
    await expect(updateUserSettings({ default_allocation:"375.5", verified_matches_only:false }))
      .resolves.toMatchObject({ default_allocation:"375.5", verified_matches_only:false });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/edgeos/api/v1/settings");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(String(init.body))).toEqual({
      default_allocation:"375.5",verified_matches_only:false,
    });
  });

  it("loads persisted preferences from the private settings API", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      timezone:"UTC",currency:"USD",default_allocation:"625",
      maximum_quote_age_seconds:90,demo_mode:true,verified_matches_only:true,
    }), { status:200,headers:{"Content-Type":"application/json","X-EDGEOS-Mode":"demo"} }));
    vi.stubGlobal("fetch", fetchMock);
    const { getUserSettings } = await import("./workflow-api");

    await expect(getUserSettings()).resolves.toMatchObject({ default_allocation:"625", maximum_quote_age_seconds:90 });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/edgeos/api/v1/settings");
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ cache:"no-store", credentials:"same-origin" });
  });

  it("exports and restores only the versioned local workspace backup type", async () => {
    const backup = new Blob(["portable-backup"], {
      type: "application/vnd.edgeos.workspace-backup+json",
    });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(backup, {
        status: 200,
        headers: {
          "Content-Type": "application/vnd.edgeos.workspace-backup+json",
          "X-EDGEOS-Mode": "demo",
        },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        status: "restored",
        restored_namespaces: ["paper_ledger", "private_user_state"],
        restart_required: false,
      }), {
        status: 200,
        headers: { "Content-Type": "application/json", "X-EDGEOS-Mode": "demo" },
      }));
    vi.stubGlobal("fetch", fetchMock);
    const {
      exportLocalWorkspaceBackup,
      restoreLocalWorkspaceBackup,
      LOCAL_WORKSPACE_BACKUP_MEDIA_TYPE,
    } = await import("./workflow-api");

    const exported = await exportLocalWorkspaceBackup();
    await restoreLocalWorkspaceBackup(exported);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/edgeos/api/v1/local-workspace/backup");
    const [restoreUrl, restoreInit] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(restoreUrl).toBe("/api/edgeos/api/v1/local-workspace/restore");
    expect(restoreInit.method).toBe("POST");
    expect(restoreInit.body).toBe(exported);
    const headers = new Headers(restoreInit.headers);
    expect(headers.get("Content-Type")).toBe(LOCAL_WORKSPACE_BACKUP_MEDIA_TYPE);
    expect(headers.get("X-EDGEOS-Confirm-Restore")).toBe("REPLACE");
  });
});
