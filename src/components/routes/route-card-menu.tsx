"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { RenameDialog } from "./rename-dialog";
import { ChangeSportDialog } from "./change-sport-dialog";
import { DeleteRouteDialog } from "./delete-route-dialog";

interface Props {
  routeId: string;
  routeName: string;
  routeSport: string;
  onRenamed: (name: string) => void;
  onSportChanged: (sport: string) => void;
  onDuplicated: (newRoute: { id: string; name: string }) => void;
  onDeleted: () => void;
}

export function RouteCardMenu({
  routeId,
  routeName,
  routeSport,
  onRenamed,
  onSportChanged,
  onDuplicated,
  onDeleted,
}: Props) {
  const [renameOpen, setRenameOpen] = useState(false);
  const [sportOpen, setSportOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [duplicating, setDuplicating] = useState(false);

  const handleDuplicate = async () => {
    setDuplicating(true);
    try {
      const res = await fetch(`/api/routes/${routeId}/duplicate`, { method: "POST" });
      if (res.ok) {
        const data = await res.json();
        onDuplicated(data);
      }
    } finally {
      setDuplicating(false);
    }
  };

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            size="sm"
            className="h-8 w-8 p-0"
            onClick={(e) => e.preventDefault()}
          >
            <span className="text-lg leading-none">&#8943;</span>
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" onClick={(e) => e.preventDefault()}>
          <DropdownMenuItem onClick={() => setRenameOpen(true)}>
            Rename
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => setSportOpen(true)}>
            Change Sport
          </DropdownMenuItem>
          <DropdownMenuItem onClick={handleDuplicate} disabled={duplicating}>
            {duplicating ? "Duplicating..." : "Duplicate"}
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            onClick={() => setDeleteOpen(true)}
            className="text-destructive"
          >
            Delete
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <RenameDialog
        open={renameOpen}
        onOpenChange={setRenameOpen}
        routeId={routeId}
        currentName={routeName}
        onRenamed={onRenamed}
      />
      <ChangeSportDialog
        open={sportOpen}
        onOpenChange={setSportOpen}
        routeId={routeId}
        currentSport={routeSport}
        onSportChanged={onSportChanged}
      />
      <DeleteRouteDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        routeId={routeId}
        routeName={routeName}
        onDeleted={onDeleted}
      />
    </>
  );
}
