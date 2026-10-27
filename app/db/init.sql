CREATE TABLE employees (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    department TEXT NOT NULL
);

CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL
);

INSERT INTO employees (name, department)
SELECT 'Nhan vien ' || g,
       (ARRAY['Kinh doanh','Ke toan','IT','Nhan su','Van hanh'])[1 + (g % 5)]
FROM generate_series(1, 200) g;

INSERT INTO users (username, password) VALUES
    ('admin', 'admin123'),
    ('alice', 'alice123'),
    ('bob',   'bob123');
