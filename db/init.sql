-- UAE Job Copilot Database Schema
-- PostgreSQL initialization script

-- Jobs table
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    source TEXT,
    title TEXT,
    company TEXT,
    location TEXT,
    url TEXT,
    description TEXT,
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    score INTEGER DEFAULT 0,
    match_reasons JSONB DEFAULT '[]',
    gaps JSONB DEFAULT '[]',
    must_apply INTEGER DEFAULT 0
);

-- Applications table
CREATE TABLE IF NOT EXISTS applications (
    job_id TEXT PRIMARY KEY REFERENCES jobs(job_id) ON DELETE CASCADE,
    status TEXT DEFAULT 'pending',
    cover_letter TEXT,
    resume_bullets TEXT,
    applied_at TIMESTAMP,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Contacts table (for cold emails)
CREATE TABLE IF NOT EXISTS contacts (
    email TEXT PRIMARY KEY,
    name TEXT,
    company TEXT,
    role TEXT,
    source TEXT DEFAULT 'manual',
    status TEXT DEFAULT 'new',
    -- new → contacted → opened → replied → interested / not_interested / bounced
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_contacted TIMESTAMP,
    notes TEXT
);

-- Cold mail send log
CREATE TABLE IF NOT EXISTS coldmail_log (
    id SERIAL PRIMARY KEY,
    email TEXT REFERENCES contacts(email) ON DELETE SET NULL,
    subject TEXT,
    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'sent',
    error_message TEXT,
    INDEX idx_coldmail_sent_at (sent_at),
    INDEX idx_coldmail_email (email)
);

-- Automation runs log
CREATE TABLE IF NOT EXISTS automation_runs (
    id SERIAL PRIMARY KEY,
    run_type TEXT NOT NULL, -- 'coldmail', 'search', 'apply', etc
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    status TEXT DEFAULT 'running', -- running, completed, failed
    results JSONB, -- {sent: 10, failed: 2, ...}
    email_sent BOOLEAN DEFAULT FALSE,
    INDEX idx_runs_type (run_type),
    INDEX idx_runs_started (started_at)
);

-- Create indexes for better performance
CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source);
CREATE INDEX IF NOT EXISTS idx_jobs_score ON jobs(score DESC);
CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status);
CREATE INDEX IF NOT EXISTS idx_contacts_status ON contacts(status);
CREATE INDEX IF NOT EXISTS idx_contacts_source ON contacts(source);

-- Create updated_at trigger function
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Apply trigger to applications
CREATE TRIGGER update_applications_updated_at 
    BEFORE UPDATE ON applications
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
