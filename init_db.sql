-- Create users table
CREATE TABLE IF NOT EXISTS users (
    user_id BIGINT PRIMARY KEY,
    username VARCHAR(255),
    first_name VARCHAR(255),
    last_name VARCHAR(255),
    total_uses INT DEFAULT 0,
    total_searches INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create searches table
CREATE TABLE IF NOT EXISTS searches (
    id SERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(user_id),
    query TEXT,
    results_count INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create track_views table
CREATE TABLE IF NOT EXISTS track_views (
    id SERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(user_id),
    track_title TEXT,
    track_artists TEXT,
    query TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create user_actions table
CREATE TABLE IF NOT EXISTS user_actions (
    id SERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(user_id),
    action_type VARCHAR(255),
    action_details TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create admins table
CREATE TABLE IF NOT EXISTS admins (
    id SERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(user_id),
    added_by BIGINT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create bot_sessions table
CREATE TABLE IF NOT EXISTS bot_sessions (
    id SERIAL PRIMARY KEY,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create indexes for faster queries
CREATE INDEX IF NOT EXISTS idx_searches_user_id ON searches(user_id);
CREATE INDEX IF NOT EXISTS idx_track_views_user_id ON track_views(user_id);
CREATE INDEX IF NOT EXISTS idx_user_actions_user_id ON user_actions(user_id);
CREATE INDEX IF NOT EXISTS idx_admins_user_id ON admins(user_id);
