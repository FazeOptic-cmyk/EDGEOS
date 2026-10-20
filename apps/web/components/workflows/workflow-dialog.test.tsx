import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { WorkflowDialog } from "./workflow-dialog";

function Harness() {
  const [open,setOpen]=useState(false);
  return <><button onClick={()=>setOpen(true)}>Open workflow</button><WorkflowDialog open={open} onClose={()=>setOpen(false)} title="Test workflow" description="Accessible workflow dialog"><input aria-label="Amount"/><button>Save</button></WorkflowDialog></>;
}

describe("WorkflowDialog",()=>{
  it("exposes dialog semantics, closes on Escape, and restores focus",()=>{
    render(<Harness/>);
    const trigger=screen.getByRole("button",{name:"Open workflow"});
    trigger.focus();fireEvent.click(trigger);
    expect(screen.getByRole("dialog",{name:"Test workflow"})).toHaveAttribute("aria-modal","true");
    fireEvent.keyDown(document,{key:"Escape"});
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});
