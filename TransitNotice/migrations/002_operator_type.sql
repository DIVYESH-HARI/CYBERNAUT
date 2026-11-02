-- Government vs private operators, so passengers can filter by who runs the service.
ALTER TABLE sources ADD COLUMN operator_type TEXT NOT NULL DEFAULT 'Government'
    CHECK (operator_type IN ('Government', 'Private'));
